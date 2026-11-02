# Inspect Command User Guide

## Overview

`piidigger inspect` is a command group of small, read-only lookup tools. None of them scan for PII or change your configuration — they answer questions like "how would a scan handle this file?", "which PII data handlers are available?", and "how many CPU cores does PIIDigger see on this machine?". They're most useful while writing or debugging a `piidigger.toml` config file, or while diagnosing why a particular file was or wasn't scanned.

## Prerequisites

### System Requirements
- PIIDigger installed (see [Installation Guide](installation.md))

### Knowledge Requirements
- Basic PowerShell or Linux/macOS shell navigation

### Required Files/Data
- `inspect file` needs a file to inspect. The other four subcommands (`archivetypes`, `datatypes`, `filetypes`, `cpu`) need nothing beyond PIIDigger itself.

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

`inspect` has five subcommands. Each does one thing:

| Subcommand | Argument | What it prints |
|---|---|---|
| `file FILE_PATH` | a file path | The extension, MIME type and encoding of `FILE_PATH`, and what a scan would do with it. |
| `archivetypes` | none | Every supported archive format name. |
| `datatypes` | none | Every registered PII data handler name. |
| `filetypes` | none | Every supported file extension, then every supported MIME type. |
| `cpu` | none | Physical and logical CPU counts PIIDigger sees on this machine. |

#### `piidigger inspect file FILE_PATH`

```powershell
piidigger inspect file C:\Reports\customers.csv
```

Shows what a scan would do with that file, using the same selection rules as the scan:

```
Extension: .csv
MIME type: text/csv
Encoding:  utf_8
Config:    built-in defaults
Outcome:   Scanned with PlaintextHandler
```

| Line | What it means |
|---|---|
| `Extension` | The extension PIIDigger goes by, in lowercase. |
| `MIME type` | The type detected from the file's content, or `(not detected)`. |
| `Encoding` | The text encoding the scan would use. It reads `(not used)` for a file that isn't read as text. |
| `Config` | The settings applied: the `-f`/`--config` file, else `piidigger.toml` in the current folder, else the built-in defaults. |
| `Outcome` | Whether the file would be scanned and how, or the reason it would not. |
| `Note` | Appears when the content looks like another supported type but the text extension decides. |

A file with a plain-text extension is always read as text. Any other file goes by its MIME type first, then its extension. See [Advanced Configuration](advanced-configuration.md#root-level-settings) for the full rules.

The encoding comes from the same detection code as the scanner, and it examines only the first 4 MB of the file. If detection fails but at least half of the sample is text, as with browser storage files, it shows `latin_1`, and the scanner reads the file that way. A file that is pure ASCII in that first 4 MB is reported as `utf_8`: UTF-8 reads ASCII identically and also handles any non-ASCII text later in the file. An empty file shows `(not detected)`, and is scanned with nothing to read. A non-empty file with no detected encoding has the outcome `Not read: text encoding could not be detected`.

This command applies the selection rules but doesn't open the file with the handler it names. So `Scanned with PdfHandler` means the file would go to that handler, not that the handler can read it: a damaged or mislabeled file can still be reported as not read during the scan. It reports a symbolic link to a file, and other non-regular files, as skipped, as a scan does inside a folder. A link to a folder, or one whose target is missing, gets a path error instead. It doesn't check `exclude_dirs` or `local_files_only`, and it doesn't account for a symbolic link further up the path. An archive's members are not listed, and members follow their own rule: they go by extension alone. If your account can't read the file, the command reports a path error. Only a file is accepted, not a folder.

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

`file` takes a path argument directly on the command line — there's no need to `cd` into the target folder first, and it accepts absolute or relative paths.

```powershell
# Relative path
piidigger inspect file .\sample-data\report.pdf

# Absolute path
piidigger inspect file "D:\Shared Files\customer-notes.txt"
```

Quote any path containing spaces, as shown above.

## Configuration

Only `inspect file` reads a configuration, the same one a scan would use: the file given with `-f`/`--config`, else `piidigger.toml` in the current folder, else the built-in defaults. An invalid configuration stops it with the same errors as `config validate`. The other subcommands report PIIDigger's registered handler and format lists, independent of any configuration.

## Output and Results

Every `inspect` subcommand prints plain text to stdout and nothing else — no log file, no results folder, no progress display. Output is meant to be read directly or piped into another command.

### Interpreting Results

- `file`: labeled lines, described under [Basic Usage](#basic-usage). If the file doesn't exist, it fails with a standard "path does not exist" error before attempting detection.
- `archivetypes` / `datatypes`: one value per line, already sorted — safe to pipe into `Select-String` or capture into a variable for a quick membership check.
- `filetypes`: two labeled sections (`Supported extensions:` then `Supported MIME types:`), each entry indented two spaces.
- `cpu`: two labeled lines, `Physical CPUs:` and `Logical CPUs:`.

## Troubleshooting

### `inspect file` reports a path error

**Symptoms:** Click reports the path doesn't exist before PIIDigger even attempts detection.

**Solutions:** Double-check the path and quoting — especially on Windows paths with spaces. The argument must be a file, not a folder.

### A file extension or MIME type I expected isn't in `inspect filetypes`

**Symptoms:** A file type you want to scan isn't accepted by `include_exts`/`include_mime` even though it's a common format.

**Solutions:** PIIDigger only scans file types with a registered file handler. If it's genuinely missing, that's a feature gap rather than a configuration mistake — check `inspect filetypes` against your config's `include_exts`/`include_mime` list before assuming the config is wrong.

### A `data_handlers` entry in my config fails validation

**Symptoms:** `piidigger config validate` reports an unrecognized `data_handlers` value.

**Solutions:** Run `piidigger inspect datatypes` and copy the exact spelling — handler names are lowercase and don't always match the PII type's common name (e.g. `pan` for payment card numbers, not `creditcard`).

### Getting Help

```powershell
piidigger inspect --help
piidigger inspect file --help
```

## Examples and Use Cases

### Example 1: Confirm a file will actually be scanned

**Use Case:** You've restricted `include_exts`/`include_mime` in a config and want to check a specific file still qualifies before running a full scan.

```powershell
piidigger inspect file -f piidigger.toml C:\Shared\quarterly-report.pdf
```

The `Outcome` line says whether the file would be scanned under that configuration, or which setting would turn it away.

**NOTE:** PIIDigger uses an external, "pure Python" (meaning it doesn't need additional OS-specific binaries, etc) package (`puremagic`) to detect the MIME type.  It's well-maintained and supported, but it can also make mistakes.

### Example 2: Diagnose a garbled scan result

**Use Case:** A text-based file's matches came back looking corrupted, and you suspect PIIDigger detected the wrong encoding.

```powershell
piidigger inspect file C:\Shared\legacy-export.txt
```

Compare the `Encoding` line against the encoding you know the file was actually saved in.

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
- Use `inspect file` on a small sample file when a scan's results for a particular file look wrong, before assuming the data handler itself is at fault.

### Performance Considerations

The subcommands don't scan a filesystem or spawn worker processes, so most are near-instant. `inspect file` always detects the MIME type, even where a scan would skip it, so it can take minutes on some large text files such as long JavaScript. Press Ctrl+C to stop it.

## Related Documentation

- [Config Command Guide](config-command.md)
- [Scan Command Guide](scan-command.md)
- [Advanced Configuration](advanced-configuration.md)
- [Installation Guide](installation.md)

---

**Next Steps:** Use what you've found here to fill in a config file with the [Config Command Guide](config-command.md), then run a scan with the [Scan Command Guide](scan-command.md).
