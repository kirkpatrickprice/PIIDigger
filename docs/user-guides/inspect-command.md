# Inspect Command User Guide

## Overview

`piidigger inspect` is a command group of small, read-only lookup tools. None of them scan for PII or touch your configuration — they answer questions like "what MIME type would PIIDigger assign to this file?", "which PII data handlers are available?", and "how many CPU cores does PIIDigger see on this machine?". They're most useful while writing or debugging a `piidigger.toml` config file, or while diagnosing why a particular file was or wasn't scanned.

## Prerequisites

### System Requirements
- PIIDigger installed (see [Installation Guide](installation.md))

### Knowledge Requirements
- Basic PowerShell or Linux/macOS shell navigation

### Required Files/Data
- `inspect mime` and `inspect encoding` need a file (or, for `mime`, a folder) to inspect. The other four subcommands (`archivetypes`, `datatypes`, `filetypes`, `cpu`) need nothing beyond PIIDigger itself.

## Getting Started

### First Steps

If you just want to see what values are valid to put in a config file, start here:

```powershell
piidigger inspect datatypes      # valid data_handlers entries
piidigger inspect filetypes      # valid include_exts / include_mime entries
piidigger inspect archivetypes   # valid archives.formats entries
```

## Core Functionality

### Basic Usage

`inspect` has six subcommands. Each does one thing:

| Subcommand | Argument | What it prints |
|---|---|---|
| `mime FILE_PATH` | a file or folder path | The MIME type PIIDigger would assign to `FILE_PATH`. |
| `encoding FILE_PATH` | a file path | The text encoding PIIDigger would use to read `FILE_PATH`. |
| `archivetypes` | none | Every supported archive format name. |
| `datatypes` | none | Every registered PII data handler name. |
| `filetypes` | none | Every supported file extension, then every supported MIME type. |
| `cpu` | none | Physical and logical CPU counts PIIDigger sees on this machine. |

#### `piidigger inspect mime FILE_PATH`

```powershell
piidigger inspect mime C:\Reports\customer-list.xlsx
```

Prints the single MIME type string PIIDigger's MIME detector assigns to that path — the same detection PIIDigger uses internally when `include_mime` filtering is in effect. `FILE_PATH` may be a file or a folder.

#### `piidigger inspect encoding FILE_PATH`

```powershell
piidigger inspect encoding C:\Reports\notes.txt
```

Reads the file and prints the text encoding PIIDigger's encoding detector would use to decode it (e.g. `utf-8`, `windows-1252`). Only accepts a file, not a folder. If the file can't be read, the command reports the OS error instead of a silent failure.

#### `piidigger inspect archivetypes`

```powershell
piidigger inspect archivetypes
```

Lists the archive format names PIIDigger knows how to open (e.g. `7z`, `tar`, `zip`), one per line, alphabetically. These are exactly the strings valid inside `archives.formats` in a config file.

#### `piidigger inspect datatypes`

```powershell
piidigger inspect datatypes
```

Lists every registered PII data handler name (e.g. `email`, `pan`), one per line, alphabetically. These are exactly the strings valid inside `data_handlers` in a config file.

#### `piidigger inspect filetypes`

```powershell
piidigger inspect filetypes
```

Prints two lists: every supported file extension (for `include_exts`), followed by every supported MIME type (for `include_mime`).

#### `piidigger inspect cpu`

```powershell
piidigger inspect cpu
```

Prints the physical and logical CPU counts PIIDigger detects, e.g.:

```
Physical CPUs: 8
Logical CPUs: 16
```

Useful for working out what a `performance` preset (`"fast"`, `"balanced"`, `"slow"`) actually translates to in worker-process count on this specific machine before committing to it in a config file — see [Advanced Configuration](advanced-configuration.md#root-level-settings).

### Working with Files

`mime` and `encoding` both take a path argument directly on the command line — there's no need to `cd` into the target folder first, and both accept absolute or relative paths.

```powershell
# Relative path
piidigger inspect mime .\sample-data\report.pdf

# Absolute path
piidigger inspect encoding "D:\Shared Files\customer-notes.txt"
```

Quote any path containing spaces, as shown above.

## Configuration

`inspect` subcommands don't read a `piidigger.toml` config file at all — they report PIIDigger's built-in detection logic and registered handler/format lists directly, independent of any scan configuration. There's nothing to configure here.

## Output and Results

Every `inspect` subcommand prints plain text to stdout and nothing else — no log file, no results folder, no progress display. Output is meant to be read directly or piped into another command.

### Interpreting Results

- `mime` / `encoding`: a single line — the detected value. If the file doesn't exist, `mime`/`encoding` fail with a standard "path does not exist" error before attempting detection (both arguments are validated as existing paths up front).
- `archivetypes` / `datatypes`: one value per line, already sorted — safe to pipe into `Select-String` or capture into a variable for a quick membership check.
- `filetypes`: two labeled sections (`Supported extensions:` then `Supported MIME types:`), each entry indented two spaces.
- `cpu`: two labeled lines, `Physical CPUs:` and `Logical CPUs:`.

## Troubleshooting

### `inspect mime` or `inspect encoding` reports a path error

**Symptoms:** Click reports the path doesn't exist before PIIDigger even attempts detection.

**Solutions:** Double-check the path and quoting — especially on Windows paths with spaces. `encoding` additionally requires the argument to be a file, not a folder; `mime` accepts either.

### A file extension or MIME type I expected isn't in `inspect filetypes`

**Symptoms:** A file type you want to scan isn't accepted by `include_exts`/`include_mime` even though it's a common format.

**Solutions:** PIIDigger only scans file types with a registered file handler. If it's genuinely missing, that's a feature gap rather than a configuration mistake — check `inspect filetypes` against your config's `include_exts`/`include_mime` list before assuming the config is wrong.

### A `data_handlers` entry in my config fails validation

**Symptoms:** `piidigger config validate` reports an unrecognized `data_handlers` value.

**Solutions:** Run `piidigger inspect datatypes` and copy the exact spelling — handler names are lowercase and don't always match the PII type's common name (e.g. `pan` for payment card numbers, not `creditcard`).

### Getting Help

```powershell
piidigger inspect --help
piidigger inspect mime --help
piidigger inspect encoding --help
```

## Examples and Use Cases

### Example 1: Confirm a file will actually be scanned

**Use Case:** You've restricted `include_exts`/`include_mime` in a config and want to check a specific file still qualifies before running a full scan.

```powershell
piidigger inspect mime C:\Shared\quarterly-report.pdf
piidigger inspect filetypes
```

Compare the printed MIME type / extension against the `include_exts`/`include_mime` lists you configured.

**NOTE:** PIIDigger uses an external, "pure Python" (meaning it doesn't need additional OS-specific binaries, etc) package (`puremagic`) to detect the MIME type.  It's well-maintained and supported, but it can also make mistakes.

### Example 2: Diagnose a garbled scan result

**Use Case:** A text-based file's matches came back looking corrupted, and you suspect PIIDigger detected the wrong encoding.

```powershell
piidigger inspect encoding C:\Shared\legacy-export.txt
```

Compare that against the encoding you know the file was actually saved in.

**NOTE::** PIIDigger uses an external Python package (`charset-normalizer`) to detect encodings.  It's a well-maintained and supported package, but it can still make mistakes.

### Example 3: Sizing worker count before choosing a `performance` preset

**Use Case:** Deciding between `"fast"`, `"balanced"`, and `"slow"` for a scan on an unfamiliar machine.

```powershell
piidigger inspect cpu
```

`"fast"` uses the logical CPU count, `"balanced"` uses ~75% of the physical count, and `"slow"` always uses one worker — `inspect cpu` tells you the actual numbers those presets will resolve to here.

**NOTE:** The difference between `physical` and `logical` cores is dependant on CPU architecture. For instance, many Intel and AMD CPUs will report twice as many logical cores based on HT or SMT features.  PIIDigger's work loads -- a mix of heavy I/O (disk access) and heavy CPU (regex processing) -- benefit from these CPU features.  That said, a `fast` scan will give all CPU priority to PIIDigger and other applications on the system will suffer.

## Best Practices

### Workflow Recommendations

- Run `inspect datatypes`, `inspect filetypes`, and `inspect archivetypes` before hand-writing a restrictive config, rather than guessing handler or extension names — an unrecognized name fails `config validate` with the full list anyway, so checking first just saves a round-trip.
- Use `inspect mime`/`inspect encoding` on a small sample file when a scan's results for a particular file look wrong, before assuming the data handler itself is at fault.

### Performance Considerations

All six subcommands are near-instant — none of them scan a filesystem or spawn worker processes — so there's no cost to checking them as often as needed while iterating on a config file.

## Related Documentation

- [Config Command Guide](config-command.md)
- [Scan Command Guide](scan-command.md)
- [Advanced Configuration](advanced-configuration.md)
- [Installation Guide](installation.md)

---

**Next Steps:** Use what you've found here to fill in a config file with the [Config Command Guide](config-command.md), then run a scan with the [Scan Command Guide](scan-command.md).
