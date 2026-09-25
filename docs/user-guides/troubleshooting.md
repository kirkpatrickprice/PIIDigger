# Troubleshooting User Guide

## Overview

This guide covers cross-cutting problems that aren't specific to one `piidigger` subcommand — where to look when something goes wrong, why PIIDigger's result counts may not match another tool's, and how to file a useful bug report. For command-specific errors (a bad flag, an invalid config value, a missing file), check that command's own guide first: [Scan Command](scan-command.md), [Config Command](config-command.md), [Inspect Command](inspect-command.md).

## Prerequisites

### System Requirements
- PIIDigger installed (see [Installation Guide](installation.md))

### Knowledge Requirements
- Basic PowerShell or Linux/macOS shell navigation

### Required Files/Data
- None

## Getting Started

### Start with the log file

Every run writes to a log file — `logs/piidigger.log` by default (configurable via `log_file` in `piidigger.toml`; see [Advanced Configuration](advanced-configuration.md)). When something looks wrong, watch it live in a second terminal while the scan runs:

```powershell
# Windows PowerShell
Get-Content logs/piidigger.log -Wait
```

```bash
# Linux/macOS
tail -f logs/piidigger.log
```

## Core Functionality

### Capturing a DEBUG-level log

The default `log_level` is `"INFO"`. For a hard-to-diagnose problem, switch to `"DEBUG"` for one run:

```powershell
piidigger config generate
# edit piidigger.toml: change log_level = "INFO" to log_level = "DEBUG"
piidigger scan -f piidigger.toml
```

**Warning:** `DEBUG` logs are large — easily hundreds of MB on a big scan. Don't leave a config running in `DEBUG` mode any longer than needed to capture the problem; switch back to `"INFO"` afterward.

## Troubleshooting

### My results look lower than another tool's

**Symptoms:** A different scanning tool reports many more matches than PIIDigger for what looks like the same data.

**Explanation:** PIIDigger reports **unique** matches per file, per data handler — not raw occurrence counts. If the same email address or card number appears 50 times in one file, it's reported once for that file. This is by design, not a bug; in testing against a dataset with hundreds of thousands of duplicated fake card numbers, deduplicated PIIDigger counts landed within a few percent of a tool that counted every occurrence.

### PIIDigger didn't flag a file I expected it to catch

**Symptoms:** A file you know contains PII wasn't included in the results.

**Solutions:** Check, in order:

1. **Is the file type supported?** Run `piidigger inspect mime <path>` and `piidigger inspect filetypes` (see [Inspect Command Guide](inspect-command.md)) to confirm the file's detected MIME type/extension has a registered handler, and isn't excluded by `include_exts`/`include_mime` in your config.
2. **Is the encoding supported?** For plain-text files, run `piidigger inspect encoding <path>`. PIIDigger only scans encodings it can reliably map onto regex-friendly text; non-Latin encodings aren't currently supported.
3. **Was the file inside an excluded directory, or too large?** Check `exclude_dirs`, and for spreadsheets, the `[spreadsheet]` blank-row/column cutoffs — see [Advanced Configuration](advanced-configuration.md).

### A scan appears hung

**Symptoms:** No progress-bar movement for a long time, and the log file shows nothing new.

**Solutions:** First, check the log for repeated timeout/termination messages — a single stuck file is handled automatically (see the "A worker seems stuck" entry in the [Scan Command Guide](scan-command.md#troubleshooting)) and shouldn't stall the whole run. If the whole process genuinely seems stuck, try **Ctrl+C** once and give it a few seconds. If that doesn't work, stop it at the OS level:

```powershell
# Windows PowerShell
Get-Process python | Stop-Process
```

```powershell
# Windows Task Manager
# Manually "End Task" on each python.exe process in the list
```

```bash
# Linux/macOS
kill $(pidof python)
```

### Getting Help

```powershell
piidigger --help
piidigger scan --help
```

## Reporting a New Issue

1. Capture a `DEBUG`-level log for the problem run (see above).
2. Open an issue on the [PIIDigger GitHub Issues page](https://github.com/kirkpatrickprice/PIIDigger/issues).
3. Attach the log file — **zip it first**, since `DEBUG` logs can be quite large.
4. You can delete the temporary config file and log once the issue is filed; the default configuration and logging level will be used again automatically.

## Related Documentation

- [Installation Guide](installation.md)
- [Scan Command Guide](scan-command.md)
- [Config Command Guide](config-command.md)
- [Inspect Command Guide](inspect-command.md)
- [Advanced Configuration](advanced-configuration.md)

---

**Next Steps:** If the problem turns out to be a configuration question rather than a bug, see [Advanced Configuration](advanced-configuration.md) for the full settings reference.
