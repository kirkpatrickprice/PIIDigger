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

Before a member is ever extracted, it has to pass two gates: it must be an actual file (not a directory, and not a symlink, hardlink, device node, or similar), and its file type has to match a file type PIIDigger has a handler for -- either by extension or by MIME type. Archives — `.tar` in particular — can contain entries that are none of those things, most notably symbolic links, and how safely that's handled currently differs by archive format:

- **`.tar` (and its compressed variants):** symlinks, hardlinks, device nodes, and FIFOs are excluded during enumeration, before any extraction decision is made — they never become a scan task at all. A symlink inside a tarball, even one crafted to point outside the archive, has no path to being followed or read by PIIDigger.
- **`.zip`:** extraction never uses a "recreate the original file type" operation — it reads a member's stored bytes and writes them into a brand-new regular file. There's no mechanism here that creates or follows a filesystem symlink, regardless of what permission metadata a zip entry carries.
- **`.7z`:** this is the one exception worth knowing about. PIIDigger's `.7z` handler does not currently filter out symlink members before extraction the way the `.tar` handler does, so a symlink entry in a `.7z` archive can reach the extraction step. In practice this is still constrained by the third-party `py7zr` library itself, which checks a symlink's target against the extraction directory and refuses to create it (raising an error instead) if the target would resolve outside that directory. That means a `.7z` symlink crafted to point at an arbitrary host path is rejected rather than followed — but the protection is coming from that dependency's own internal check, not from PIIDigger's own member-type filtering, so it's a narrower guarantee than the one `.tar` handling provides directly.

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
- [Installation Guide](installation.md)
