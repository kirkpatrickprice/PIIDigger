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

- **Scan Configuration** — the performance preset and worker count, the directories being scanned, whether OS sleep-prevention is active, and a reminder that Ctrl+C stops the scan.
- **Progress** — three bars (Dirs, Files, Bytes: scanned vs. found, with percentage and elapsed time), a running "Results Found" counter, and an ETA once at least 200 tasks have completed. The ETA reads `--:--` before that threshold — that's expected on short scans, not a stall.
- **Events** — the most recent warnings and errors (timeouts, unreadable files, skipped archive members), newest additions at the bottom.

When `scan`'s output isn't a terminal — e.g. piped to a file, redirected in CI, etc. — the live display is skipped entirely, and a single plain-text summary line is printed at the end instead (e.g. `Scan complete.  dirs_scanned=1,204  files_scanned=38,502  ...`).

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
