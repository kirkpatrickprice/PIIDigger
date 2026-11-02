# Scan Command User Guide

## Overview

`piidigger scan` is the command that actually walks your filesystem, opens files (and archives), and looks for PII. It's also the default action — running `piidigger` with no subcommand is exactly the same as running `piidigger scan`.

This guide covers the `scan` command itself: its flags, what happens while a scan is running, where the results end up, and how to read the live progress display. For what goes *into* a config file, see [Advanced Configuration](advanced-configuration.md); for how archive files are handled internally, see [Archive Handling](archive-handling.md).

## Prerequisites

### System Requirements
- PIIDigger installed (see [Installation Guide](installation.md))
- Enough free disk space in the OS temp folder to hold one extracted archive member per worker process, if scanning archives (see [Archive Handling](archive-handling.md))

### Knowledge Requirements
- Basic PowerShell or Linux/macOS shell navigation
- Optional: a `piidigger.toml` config file (see [Advanced Configuration](advanced-configuration.md)) if you don't want the built-in defaults

### Required Files/Data
- None. `scan` runs with sensible built-in defaults if no configuration file is present.

## Getting Started

### First Steps

The simplest way to run a scan is with no arguments at all:

```powershell
piidigger
```

This is shorthand for `piidigger scan`, and behaves the same whether you type the subcommand or not. With no config file in the current directory, it uses PIIDigger's built-in defaults: every Windows drive letter or Linux/MacOS mount, every supported file type and archive format, every registered PII data type, the `"balanced"` performance preset, and results written to `.\piidigger-results\` in all supported output formats.

## Core Functionality

### Basic Usage

#### Primary Command

```powershell
piidigger scan
```

If a file named `piidigger.toml` exists in the current directory, `scan` picks it up automatically — no flag required. If it doesn't exist, `scan` falls back to built-in defaults. Either way, nothing more is needed to start scanning.

#### Command Options

| Flag | Description |
|---|---|
| `-f, --config FILE` | Use a specific TOML configuration file instead of the auto-detected `piidigger.toml`. If the path doesn't exist, `scan` fails immediately with an error rather than silently falling back to defaults. |
| `-d, --default-config` | Ignore any config file entirely (even one named `piidigger.toml` in the current directory) and use built-in defaults for this run. |
| `--no-archives` | Skip all archive files (`.zip`, `.7z`, `.tar` and its compressed variants) for this run only, regardless of what `[archives].enabled` says in the config file. |

```powershell
# Use a specific config file
piidigger scan -f finance-share.toml

# Ignore any config file present and use built-in defaults
piidigger scan -d

# Run with your usual config, but skip archives just this once
piidigger scan -f piidigger.toml --no-archives
```

`-f` and `-d` are meant to be used one at a time — `-d` always wins if both a config file exists and a scan is otherwise about to use it, since it explicitly tells `scan` to bypass config discovery.

#### Working with Files

`scan` doesn't take file or folder arguments directly — where it looks is controlled entirely by `start_dirs` and `exclude_dirs` in the configuration (built-in defaults scan every available drive/mount). See [Advanced Configuration](advanced-configuration.md#start_dirs-and-exclude_dirs) to point a scan at specific folders instead of the whole system or to exclude certain drive letters/folders.

## Configuration

`scan` reads its settings from the same `Config` model documented in full in [Advanced Configuration](advanced-configuration.md) — start/exclude directories, file types, data handlers, performance preset, timeouts, and output settings. This guide only covers the command-line surface (`-f`, `-d`, `--no-archives`); everything that changes *what* gets scanned lives in the config file.

### Default Settings

With no config file, `scan` will use the following default configuration:
* Scan all available Windows drive letters or Linux/MacOS disk partitions
* Scan for all supported file types
* Scan inside all supported archive files
* Scan for all supported sensitive data types
* Use the "Balanced" profile (75% of physical cores)
* Produce output files in all supported versions under the `<current_folder>/piidigger-results/` folder
* Produce a log in `current_folder/logs` using the `INFO` log level setting 

### Custom Configuration

Generate and edit a config file with `piidigger config generate`, then point `scan` at it with `-f` — see the [Config Command Guide](config-command.md) and [Advanced Configuration](advanced-configuration.md) for details.

## Output and Results

### The Live Progress Display

While attached to an interactive terminal, `scan` shows a live three-panel display:

- **Scan Configuration** — the performance preset and worker count, the directories being scanned, the log file, whether OS sleep-prevention is active, and a reminder that Ctrl+C stops the scan.
- **Progress** — three bars (Dirs, Files, Bytes: scanned vs. found, with percentage and elapsed time), a running "Results Found" counter, and an ETA once at least 200 tasks have completed. The ETA reads `--:--` before that threshold — that's expected on short scans, not a stall.
- **Events** — the most recent warnings and errors (timeouts, unreadable files, skipped archive members), newest additions at the bottom.

When `scan`'s output isn't a terminal — e.g. piped to a file, redirected in CI, etc. — the live display is skipped entirely. The summary below is still printed at the end.

### The End-of-Scan Summary

Every scan ends with a short summary, one fact per line:

```text
Scan complete in 0:41:13. Found 108,108 results: 60,735 pan, 47,373 email.
  Scanned: 28,036 folders, 108,826 files, 6.4 GB

Not fully scanned. See the log for details: logs/piidigger-20261031-153257.log
       51  files, folders or archive members: 41 errors, 10 over the memory limit
      562  files not read: text encoding could not be detected (search the log for "text encoding")
       14  PDFs with no extractable text (search the log for "no extractable text")
       16  archive members rejected: 12 too deep to reach, 3 over the archive's total size, 1 unsafe
        5  archive members encrypted
        7  nested archives not opened

Skipped by design:
      120  files of unsupported types
   13,787  archive members of unsupported types
```

Rows with nothing to report are left out, so a clean run is just the first two lines.

- **Scanned** counts only what was actually read.
- **Not fully scanned** lists content that might hold PII but wasn't read. A file with an error may still have been partly read, and anything found in the part that was read stays in your results. A PDF with no extractable text opened without error but held no text, usually because its pages are scanned images. PIIDigger doesn't read text from images, so it counts these under "Scanned" and lists them here too. If this section lists rejected archive members, see [The summary says archive members were rejected](troubleshooting.md#the-summary-says-archive-members-were-rejected).
- **Skipped by design** lists file types PIIDigger has no reader for, or that `include_exts` left out. It isn't a problem unless you expected those types to be scanned.

A scan with rows under "Not fully scanned" still exits with code 0, as long as it ran to the end. A full-disk scan nearly always meets a locked file, so the exit code wouldn't tell you much. Check the summary or the log when you need to know what was missed.

The same summary is written to the end of the log file. The log also lists the unsupported file types: common document types such as `.doc` and `.pptx` first, then up to 15 others, largest first. The totals, and anything skipped by design, are logged at INFO. Everything under "Not fully scanned", and any results file that stopped after an error, is logged at WARNING. The per-file lines for undetected text encodings and PDFs with no extractable text are logged at INFO, so with `log_level = "WARNING"` the summary counts them but the log doesn't name them.

PIIDigger leaves a few things out of the counts altogether, because they hold no content of their own: symlinks, and the `$I` metadata files in a Windows Recycle Bin. The deleted files themselves are scanned. A Windows junction such as `Application Data` that refuses access isn't reported as a failure when its target is inside the folders being scanned, because the target is scanned under its real name. Any other folder that refuses access, including a folder-mounted volume, is reported. A junction that can be read is scanned like any other folder, even when it leads outside your start directories, so its contents may appear twice in your results. A junction pointing back at a folder the scan came through is skipped, which stops loops. Many folders that all link to each other can still be listed many times over, and findings repeat with each listing.

An archive counts as opened, not as a scanned file. Its members count as files.

### Output Files

Results are written to the folder set by `[results].path` (default `piidigger-results`, created if missing), in the format(s) set by `[results].formats` (default: all three):

| Format | File(s) | Notes |
|---|---|---|
| CSV | `<hostname>-<YYYYMMDD-HHMMSS>.csv` | One row per matched value. Columns: `source_path`, `source_member_path`, `source_depth`, `source_container_type`, `handler`, `match_type`, `value`. |
| JSON | `<hostname>-<YYYYMMDD-HHMMSS>.json` and `.jsonl` | The `.json` file is a full array, written once at the end of the run. The `.jsonl` file is written incrementally during the scan (one JSON object per line) — if you need to inspect partial results from a very long scan, or the scan is killed with SIGKILL before it can finish writing the `.json` array, `.jsonl` is the file to check. |
| Text | `<hostname>-<YYYYMMDD-HHMMSS>.txt` | Pipe-separated: `source_path \| handler \| match_type \| value`, with `member=`, `depth=`, and `container=` tokens appended when the finding came from inside an archive. |

Filenames are stamped with the hostname and start time, so repeated runs never overwrite each other's output.

### Interpreting Results
Here are a few notes about looking at the results:

* Every finding carries lineage: `source_path` is always the on-disk file PIIDigger opened. 
* For a match found on-disk, `source_member_path`, `source_depth`, and `source_container_type` are all empty/zero. 
* For a match found inside an archive, `source_path` is the archive itself
    * `source_member_path` is the path of the matching entry *inside* that archive
    * `source_depth` is `1` (or greater once nested-archive support lands)
    * `source_container_type` names the archive format (e.g. `"zip"`). 
* `handler` names which PII data handler matched (e.g. `pan`, `email`), and `match_type` is the specific pattern within that handler (e.g. `visa`, `amex`, etc), with `value` being the matched text itself.

## Troubleshooting

### Admin privilege prompt

**Symptoms:** `scan` prints `Admin user not detected.  A full disk scan may not be possible.  Continue (Y/n) [10s]:` and waits.

**Solutions:** This appears when `admin_check = true` (the default) and PIIDigger isn't running elevated — a non-admin scan may not be able to read every file on the system. Press Enter or type `y` to continue anyway, or `n` to abort. If you don't respond within 10 seconds, the scan continues automatically. Set `admin_check = false` in your config to skip this prompt on future runs; it's also skipped automatically when `scan`'s input isn't an interactive terminal (e.g. scheduled tasks, CI).

Note: For a full-disk scan on Windows, it's necessary to start the Powershell prompt as Administrator in addition to using a user account with Local Administrator permissions.  On Linux/MacOS, running PIIDigger as `root` is sufficient.

### `scan` exits immediately with "config file ... not found"

**Symptoms:** `Error: config file 'foo.toml' not found.`

**Solutions:** This only happens with `-f`/`--config` pointing at a path that doesn't exist — unlike the automatic `piidigger.toml` lookup, an explicitly named file that's missing is treated as a mistake, not a signal to fall back to defaults. Fix the path, or drop `-f` if you meant to use defaults (or add `-d` to be explicit about it).

### `scan` exits immediately with a configuration error

**Symptoms:** `Error: invalid configuration in piidigger.toml: ...`

**Solutions:** The config file failed validation before the scan could start. Run `piidigger config validate` on the same file to see the full error and fix it there — see the [Config Command Guide](config-command.md) and [Advanced Configuration](advanced-configuration.md).

### A worker seems stuck / the Events panel shows timeouts

**Symptoms:** An `Events` entry reports a task that timed out.

**Solutions:** A single file, directory, or archive member that doesn't finish within `default_timeout_seconds` (default 30s) is terminated and replaced automatically — the scan keeps going, and that one item is recorded as timed out rather than scanned. If you're seeing many of these, the timeout may be too short for your storage (e.g. a slow network share); raise `default_timeout_seconds` in the config. See [Advanced Configuration](advanced-configuration.md).

### Sleep prevention shows "Unavailable"

PIIDigger asks the operating system to stay awake during a scan. The Scan Configuration panel shows the result:

- **Active** — the desktop or operating system accepted the request.
- **Active (logind)** — on Linux, the desktop could not take the request, so PIIDigger holds a lock from `logind` instead. This is normal under `sudo`. Run `systemd-inhibit --list` to see the lock.
- **Unavailable (no desktop session or logind lock)** — on Linux, neither route worked. This is common in containers, and for a standard user over SSH, whom `logind` usually refuses. Running with `sudo` avoids the refusal. The log file records the reason.
- **Unavailable** — on Windows or macOS, the request failed. The log file records the reason.

A scan runs the same either way, but the machine may go to sleep and pause it. Change the machine's power settings for a long scan if the status is unavailable.

The machine can still suspend while the status is active:

- Closing a laptop's lid usually still suspends it. Most Linux desktops set `LidSwitchIgnoreInhibited=yes`.
- On older systemd releases, such as 255, a `logind` lock does not stop a suspend requested by the same user. A desktop's idle timer running as that user may do this, so check its power settings. This does not apply under `sudo`, where root holds the lock.

### Stopping a scan early

Press **Ctrl+C** once. PIIDigger stops feeding new work to the workers, lets in-flight tasks wind down, flushes whatever results have been found so far to the output sinks, and exits. Output files up to that point are valid and usable — they just won't include anything past the point of interruption.

If Ctrl+C doesn't respond within a few seconds and the process genuinely seems stuck, fall back to stopping it at the OS level — see [Troubleshooting](troubleshooting.md#a-scan-appears-hung) for the platform-specific commands.

### Getting Help

```powershell
piidigger scan --help
```

## Examples and Use Cases

### Example 1: Quick default scan

**Use Case:** First time using PIIDigger, or a one-off check with no config file.

```powershell
piidigger
```

### Example 2: Repeatable scan against a named profile

**Use Case:** Regularly scanning a specific file share with a tuned configuration.

```powershell
piidigger config generate finance-share.toml
# ... edit finance-share.toml: start_dirs, data_handlers, etc ...
piidigger config validate finance-share.toml
piidigger scan -f finance-share.toml
```

### Example 3: One-off run without archive scanning

**Use Case:** Your usual config has archives enabled, but this run is against a share with unusually large or deeply-compressed archive files and you want to skip them without editing the file.

```powershell
piidigger scan -f piidigger.toml --no-archives
```

## Best Practices

### Workflow Recommendations

- Run `piidigger config validate` after editing a configuration file.  It will provide verbose messages about any problems it finds.
- Use `-d` when you want to sanity-check PIIDigger's built-in defaults against a small test folder before trusting a custom config file on the real target.
- Keep an eye on the Events panel during a run — repeated timeouts or skipped-archive warnings are worth investigating rather than ignoring, especially before treating the results as complete.

### Performance Considerations

- The `performance` config setting controls worker count (`"fast"` = one per logical core, `"balanced"` = ~75% of physical cores, `"slow"` = one worker total). More workers means faster wall-clock time but more simultaneous disk/CPU load — `"slow"` is worth using on a shared or production system you don't want to visibly impact.
- Scans against network shares are usually I/O-bound, not CPU-bound — a higher `performance` preset won't help as much there as it does on local disks, and `default_timeout_seconds` may need to be raised to accommodate slower reads.

## Related Documentation

- [Config Command Guide](config-command.md)
- [Inspect Command Guide](inspect-command.md)
- [Advanced Configuration](advanced-configuration.md)
- [Archive Handling](archive-handling.md)
- [Troubleshooting](troubleshooting.md)
- [Installation Guide](installation.md)

---

**Next Steps:** Generate and tune a configuration file with the [Config Command Guide](config-command.md), or dig into every available setting in [Advanced Configuration](advanced-configuration.md).
