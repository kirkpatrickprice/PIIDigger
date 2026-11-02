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

Each run writes to a log file named for its start time — `logs/piidigger-<YYYYMMDD-HHMMSS>.log` by default, with the same timestamp as that run's results files (configurable via `log_file` in `piidigger.toml`; see [Advanced Configuration](advanced-configuration.md)). The live display shows this run's log name in its Scan Configuration panel. When something looks wrong, watch it live in a second terminal while the scan runs. These commands open the newest log with the default name:

```powershell
# Windows PowerShell
Get-ChildItem logs/piidigger-*.log | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content -Wait
```

```bash
# Linux/macOS
tail -f "$(ls -t logs/piidigger-*.log | head -1)"
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

1. **Is the file type supported?** Run `piidigger inspect file <path>` (see [Inspect Command Guide](inspect-command.md)). It shows the file's extension and detected MIME type, and whether your config lets a scan read it. A plain-text extension is always read as text, so a real PDF named `report.txt` has its PDF text missed. Rename the file to its real extension and scan it again.
2. **Is the encoding supported?** For plain-text files, the same command shows the encoding. PIIDigger only scans encodings it can reliably map onto regex-friendly text; non-Latin encodings aren't currently supported.
3. **Was the file inside an excluded directory, or too large?** Check `exclude_dirs`; for spreadsheets, the `[spreadsheet]` blank-row/column cutoffs; and for plain-text files, the `[plaintext] max_scan_mb` cutoff (only the first 64 MB is scanned by default) — see [Advanced Configuration](advanced-configuration.md).

### Why am I seeing PDF pages that could not be read in the log?

**Symptoms:** The log has a line like `1 of 170 PDF pages could not be read, first page 46: LimitReachedError: Reached maximum number of tokens per stream`, or `PDF could not be read: PdfReadError`.

**Explanation:**

- **`N of M PDF pages could not be read`:** PIIDigger scanned the other pages and kept those findings. The message names the first page it couldn't read; if N is more than 1, others were skipped too.
- **`PDF could not be read: …`:** PIIDigger read nothing from this file. With `after page N` in the message, it scanned the pages up to that point.
- **`Reached maximum number of tokens`, with or without `per stream`:** that page is unusually complex, often a detailed drawing. PIIDigger limits this based on how much memory each worker may use, so that one page is less likely to stop the worker and lose the whole file's results.
- **Other errors:** these usually mean that part of the PDF is damaged. Other `LimitReachedError` messages are limits that the settings below don't change. PIIDigger doesn't log the details of parse errors, because they can quote the document's content.

**Solutions:** For `Reached maximum number of tokens`, with or without `per stream`, or for `PDF could not be read: PdfReadError` on a PDF that opens normally elsewhere, raise `max_worker_memory_mb` in a [custom config](advanced-configuration.md) and scan the file again. Each worker can then use more memory, so check that your machine has room for it across all workers. The second case may have another cause, in which case raising it won't help.

```toml
max_worker_memory_mb = 2048
```

Very complex pages also take longer. If the log then shows the file timing out, raise `default_timeout_seconds` too.

If you want the details, including the measurements behind the limits, see [PDF Memory Limits](../architecture/filehandlers/pdf-memory-limits.md).

### The summary says archive members were rejected

**Symptoms:** The end-of-scan summary has a row like `20,571  archive members rejected: 20,571 over a member size or count limit`.

**Find it:** Search the log for `member(s) not scanned`. Each archive and limit gets its own WARNING line, which names the archive, the setting and a few example members:

```text
archive /backups/home.tar.gz: 20571 member(s) not scanned: max_members (10,000) reached ('home/a.yml', 'home/b.yml', 'home/c.yml' and 20,568 more)
```

**Explanation:** These limits protect the scan from hostile archives and control how long a large archive takes. PIIDigger checks an archive's members in the order they are stored, so third-party files stored early can use up a limit before your own files are reached.

| Summary wording | Setting named in the log line |
|---|---|
| `over a member size or count limit` | `max_members` or `max_member_uncompressed_size_mb` |
| `over the archive's total size` | `max_total_uncompressed_size_mb` |
| `too deep to reach` | `max_decompress_depth_mb` |

**Solutions:** Raise the setting in the `[archives]` section of a [custom config](advanced-configuration.md) for a run against that archive. For `max_members`, go above the members scanned plus those rejected. In a 10,000-member scan with 20,571 rejected, that means more than 30,571:

```toml
[archives]
max_members = 31000
```

Expect a longer run. Another limit may apply once this one is out of the way, and the log names it in the same way. Only raise these limits for archives you trust. The `unsafe` wording means path traversal or a likely archive bomb, and no setting changes those checks.

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
