# Archive Handling User Guide

## Overview

PIIDigger can look inside archive files (`.zip`, `.7z`, `.tar` and its compressed variants) without you having to extract them yourself first. This guide explains what actually happens on disk while that scanning takes place — one member at a time extraction, secure deletion of each extracted file immediately after it's scanned, and the residual-data risk if PIIDigger is interrupted abnormally.

This is written for a moderately technical reader — someone deciding whether archive scanning is safe to run against sensitive data, or investigating what a scan left behind after an unexpected shutdown. See [Advanced Configuration](advanced-configuration.md) for the `[archives]` settings that control this behavior (enabling/disabling it, size limits, formats).

## How Archive Scanning Works

PIIDigger never extracts a whole archive at once. For each archive it finds:

1. It first lists the archive's members (filenames and sizes) without extracting any content — this is how size limits and encryption/path-traversal checks are applied before anything touches disk.
2. Each member that passes those checks **and** is a file type supported by PIIDigger becomes its own scan task. A worker process picks up that task, extracts **just that one member** to a temporary file, scans it, and then deletes that temporary file — before moving on to the next member.

Because each member is its own task, and PIIDigger typically runs several worker processes at once (see the `performance` setting in [Advanced Configuration](advanced-configuration.md)), a handful of members can be on disk in their extracted form simultaneously — one per active worker — but never the full contents of the archive at once.

### Symlinks and other non-regular members

Before a member is ever extracted, it has to pass two gates: it must be an actual file (not a directory, and not a symlink, hardlink, device node, or similar), and its file type has to match a file type PIIDigger has a handler for -- either by extension or by MIME type. Archives — `.tar` in particular — can contain entries that are none of those things, most notably symbolic links. All three supported archive formats are covered:

- **`.tar` (and its compressed variants):** symlinks, hardlinks, device nodes, and FIFOs are excluded during enumeration, before any extraction decision is made — they never become a scan task at all.
- **`.zip`:** entries whose stored metadata marks them as a Unix symlink are excluded the same way during enumeration. Independently of that check, extraction here never uses a "recreate the original file type" operation anyway — it reads a member's stored bytes and writes them into a brand-new regular file, so there's no mechanism that could create or follow a filesystem symlink regardless.
- **`.7z`:** entries the library reports as a symlink are excluded during enumeration, matching the `.tar` and `.zip` behavior.

In all three cases, a symlink entry never becomes an extraction target, so it has no path to being followed or read by PIIDigger — the check happens up front, not as a side effect of the underlying library's own extraction safeguards.

## Guarding Against Zip Bombs and Resource Exhaustion

A malicious or corrupt archive can claim to contain far more data than is reasonable to extract — a small file that expands to gigabytes, an archive with hundreds of thousands of entries, or a handful of members that together would exhaust disk space. PIIDigger checks an archive's own declared member metadata (names, and their compressed/uncompressed sizes) during the initial listing pass — before extracting anything — and rejects members that look unsafe. These limits live under `[archives]` in the config file; see [Advanced Configuration](advanced-configuration.md) for the full settings reference.

- **Member count cap** — `max_members` (default `10000`). Once this many members have been accepted from one archive, enumeration of that archive stops entirely; every remaining member is counted as skipped without being evaluated individually.
- **Per-member size cap** — `max_member_uncompressed_size_mb` (default `512`). Any single member whose declared uncompressed size exceeds this is skipped, regardless of how small it is compressed.
- **Compression-ratio heuristic** — a fixed check, not a separate config setting: any member whose declared uncompressed size is more than **1,000 times** its compressed size is rejected outright as a probable bomb, even if it would otherwise fit under the per-member size cap. Highly repetitive data (the classic zip-bomb technique) compresses to a tiny fraction of its expanded size, so this catches bombs that a size cap alone could miss if the cap were set generously.
- **Running total cap** — `max_total_uncompressed_size_mb` (default `8192`). PIIDigger tracks the combined declared uncompressed size of every member accepted so far from one archive. Once accepting the next member would push that running total over the cap, that member is skipped — but enumeration continues, so a smaller member later in the archive can still be accepted if it fits.

Because all four checks run against the archive's own header metadata during the up-front listing pass, a member that fails any of them is never extracted, and its data never touches disk. This is the same listing pass described in [How Archive Scanning Works](#how-archive-scanning-works) — size and ratio safety is one of the things that pass exists to establish before any extraction is allowed to happen.

If you're scanning archives from a source you'd consider untrusted — say, a file share fed by external uploads — you can tighten these below their defaults for that run; see the "Disable or tune archive scanning" scenario in [Advanced Configuration](advanced-configuration.md#disable-or-tune-archive-scanning) for an example. If archive scanning isn't something you want to expose to untrusted input at all, `archives.enabled = false` in the config or the `--no-archives` run-time flag skip archive handling entirely for that run.

## The Temporary Workspace

At the start of every run, PIIDigger creates one temporary folder for that run only, inside the OS's normal temp location (`%TEMP%` on Windows, `/tmp` on Linux/macOS). The path looks like `piidigger_<random>` and is written to the run's log file as `temp workspace: <path>` — that log line is the authoritative record of where a given run's scratch files lived.

That workspace is automatically added to the scan's own exclusion list, so PIIDigger never scans its own temporary files as if they were part of the target filesystem.

Inside it, each individual scan task gets its own subfolder (named after the task's ID), and that's where a member is extracted to while it's being read. Nothing archive-related is ever written outside this per-run folder.

## Secure Deletion of Extracted Members

As soon as a worker finishes scanning one extracted member — whether the scan succeeded or failed — it overwrites that file's contents before deleting it, rather than just unlinking it and leaving the data recoverable from unallocated disk space. This happens immediately, per member, not batched at the end of the archive or the end of the scan.

The overwrite is two passes:

1. Zero-fill the entire file.
2. Overwrite it again with random bytes.

Each pass is flushed and synced to storage before moving to the next, to maximize the chance the operating system actually writes it out rather than holding it in a cache. Only after both passes complete is the file unlinked.

### Why this is "best-effort," not a guarantee

This overwrite approach is fully effective on traditional spinning-disk (HDD) storage, where a file's data lives at a fixed physical location that a same-address overwrite will genuinely replace.

**On SSDs and other flash-based storage, it is not a guarantee.** SSD firmware uses wear-leveling: a write to the same logical file location can be redirected to a different physical flash block to spread wear evenly across the drive, rather than reusing the original cells. The original data's physical remnants can persist on the drive — invisible to the filesystem, but potentially recoverable with direct hardware access — until the drive's own garbage collection eventually reclaims that block.

This is a **hardware/firmware limitation**, not something any application-level overwrite or even an OS can fully control. PIIDigger performs the overwrite unconditionally anyway, because it's cheap insurance on HDDs and it's still the best a userspace application can do on SSDs, but you should not treat it as forensic-grade sanitization on flash storage. If that level of assurance matters for your environment, rely on full-disk encryption at rest and/or the drive's own vendor secure-erase/sanitize command instead.

## Cleanup at the End of a Normal Run

When a scan finishes normally, PIIDigger removes the entire per-run temporary folder as a final sweep. By that point it should already be empty — every task cleans up its own extracted file(s) as soon as it finishes — but this step also removes the now-empty folder structure itself.

## Risk: PII Left Behind After an Abnormal Termination

Both cleanup mechanisms above — the per-member secure delete and the final folder removal — are tied to PIIDigger's normal control flow. They rely on code actually getting a chance to run. There are two situations where that doesn't happen:

- **A worker is forcibly stopped mid-task.** If a single file or archive member takes longer than `default_timeout_seconds` to process, PIIDigger's coordinator terminates that worker process outright and replaces it, to keep a single stuck file from stalling the whole scan. If that worker was in the middle of scanning an extracted archive member when it was terminated, the secure-delete step for that specific member never runs — the extracted file is left behind, unwiped, in that task's temp subfolder.

    The per-run temporary folder will still be removed, but if the task was working on an archive member's data, that file was not subjected to the secure delete processes above.
- **The whole PIIDigger process is killed or crashes.** Force-closing the terminal, `taskkill /F`, a SIGKILL, a system crash, or a power loss during an archive scan all skip both the per-member cleanup and the final temp-folder removal. Whatever had been extracted up to that point remains in plaintext in the per-run temp folder.

In either case, the practical consequence is the same: extracted content — which may contain the exact PII the scan was looking for — can persist unencrypted on disk in the OS temp directory, outside of PIIDigger's control, until something else removes it (a reboot, a disk-cleanup policy, or manual deletion).

**If a scan involving archives is interrupted abnormally**, check the run's log file for the `temp workspace: <path>` (e.g. `/path/to/temp/piidigger_5gh7fd`) line recorded at startup, and treat that folder as needing manual review/deletion rather than assuming PIIDigger cleaned up after itself. A normal (non-crashed) run will have already removed that folder entirely, so if you find it still present after a run you expected to complete, that's a sign of an abnormal termination worth investigating.

## Related Documentation

- [Advanced Configuration](advanced-configuration.md) — `[archives]` settings: enabling/disabling archive scanning, size limits, and supported formats
- [Scan Command Guide](scan-command.md) — the `--no-archives` flag
- [Inspect Command Guide](inspect-command.md) — `inspect archivetypes` to list supported formats
- [Installation Guide](installation.md)
