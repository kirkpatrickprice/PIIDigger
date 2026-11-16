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

Like all tools of this nature, PIIDigger also makes its own assumptions about where to spend time.  For instance, the default configuration steers clear of operating system folders which are known to contain only program files (`/usr/bin`, `/usr/sbin`, `C:\Program Files`, etc).  All of these assumptions can be changed by creating your own configuration with `piidigger config generate`.

Finally, PIIDigger's primary detection method is regular expressions and is therefore  predominantly bounded by CPU resources.  The range of what is a potentially valid email address is quite complex.  To balance both detections and performance, PIIDigger makes some assumptions, especially about the length of the various parts of an email address.  These are not currently adjustable via a configuration file.

### PIIDigger didn't flag a file I expected it to catch

**Symptoms:** A file you know contains PII wasn't included in the results.

**Solutions:** Check, in order:

1. **Is the file type supported?** Run `piidigger inspect file <path>` (see [Inspect Command Guide](inspect-command.md)). It shows the file's extension and detected MIME type, and whether your config lets a scan read it. A plain-text extension is always read as text, so a real PDF named `report.txt` has its PDF text missed. Rename the file to its real extension and scan it again.
2. **Is the encoding supported?** For plain-text files, the same command shows the encoding. PIIDigger only scans encodings it can reliably map onto regex-friendly text; non-Latin encodings aren't currently supported.
3. **Was the file inside an excluded directory, or too large?** Check `exclude_dirs`; for spreadsheets, the `[spreadsheet]` blank-row/column cutoffs; and for plain-text files, the `[plaintext] max_scan_mb` cutoff (only the first 64 MB is scanned by default) — see [Advanced Configuration](advanced-configuration.md).
4. **Was the file read, but a card number in it not recognized?** See [A card number in a file wasn't found](#a-card-number-in-a-file-wasnt-found).

### A card number in a file wasn't found

**Symptoms:** A file was scanned, but a card number you can see in it isn't in the results.

**Explanation:** The `pan` data handler reports a number only when it looks like a card in the way card brands print and issue them, and passes the Luhn checksum that every real card number carries. It checks:

- **Brand and range:** the first digits must belong to a card brand:

  | Brand | First digits | Length |
  |---|---|---|
  | Visa (`visa`) | 4 | 16 |
  | Mastercard (`mc`) | 51–55, 2221–2720 | 16 |
  | Discover (`discover`) | 6011, 644–649, 65 | 16 |
  | American Express (`amex`) | 34, 37 | 15 |
  | JCB (`jcb`) | 3528–3589 | 16 |
  | JCB (`jcb`), discontinued | 2131, 1800 | 15 |

  Other brands, such as UnionPay, aren't detected yet.
- **Grouping:** the digits must be written together, or in the brand's own groups with the same separator throughout. The only separators are a space and a hyphen. That means 4-4-4-4 for 16-digit cards (`4111 1111 1111 1111`, `4111-1111-1111-1111`) and 4-6-5 for 15-digit cards (`3782 822463 10005`). A number split another way, such as `41111111 11111111`, or with mixed separators, such as `4111-1111 1111-1111`, isn't reported.
- **Edges:** the number must not run on into other digits. A digit, or a hyphen, directly before or after it means it is part of a longer value, such as an ID. A dot counts only when it joins the number to another digit, as in a decimal (`1.4111111111111111`). So a card after `No.` or before a full stop is found.
- **ASCII digits only:** the number must be written with the digits `0`–`9`. A card number typed with another script's digits isn't found. That includes fullwidth digits (`４１１１…`), which Japanese and Chinese input methods produce, and Arabic-Indic or Persian digits (`٤١١١…`, `۴۱۱۱…`). This is a known limitation.

Line breaks, tabs and spreadsheet cell boundaries reach the `pan` handler as spaces. So values on separate lines or in adjacent cells can occasionally combine into what looks like a card number. Those appear in the results as findings to check, rather than being missed.

**Solutions:** Run `piidigger inspect file <path>` first, to confirm the file was read. If it was, compare the number with the rules above. A number in another script's digits, or in a grouping no card brand uses, won't be reported. To have such numbers found, open a feature request on the [issue tracker](https://github.com/kirkpatrickprice/PIIDigger/issues).

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

### I just want to find credit card numbers -- why is it so slow?

**Symptoms:** A scan takes hours, and you only care about card numbers (PANs).

**Explanation:** By default, PIIDigger looks for every data type it supports. Each one adds a pass of pattern matching over every file it reads. Email address matching is the most CPU-heavy of these (see [My results look lower than another tool's](#my-results-look-lower-than-another-tools)). If you don't need email results, that work is wasted.

**Solutions:** Create a configuration that looks only for the `pan` data type:

1. Generate a configuration file:

    ```powershell
    piidigger config generate piidigger.toml
    ```

2. Open `piidigger.toml` and change the `data_handlers` line to:

    ```toml
    data_handlers = ["pan"]
    ```

3. Run the scan with that file:

    ```powershell
    # piidigger.toml is picked up automatically in the current folder
    piidigger scan

    # piidigger.toml is in a different directory or named something different
    piidigger scan -f C:\<path-to>\piidigger-panonly.toml
    ```

Run `piidigger inspect datatypes` to list the data types you can name here. For other ways to shorten a scan, such as excluding folders you don't need, see [Advanced Configuration](advanced-configuration.md).

### A config file I edited is rejected at line 1, column 1

**Symptoms:** `piidigger config validate` or `piidigger scan -f` fails with `Invalid statement (at line 1, column 1)`, though line 1 of the file looks fine.

**Explanation:** Some editors and scripts save files as "UTF-8 with BOM", which adds an invisible marker at the start of the file. PIIDigger can't read a config file that starts with one. Notepad and VS Code should be safe, because both save as plain UTF-8 by default.

**Solutions:** Save the file as plain "UTF-8" and validate it again. See [Advanced Configuration](advanced-configuration.md#config-validate-reports-invalid-statement-at-line-1-column-1) for details.

### The summary says folders or files were not readable

**Symptoms:** The summary's "Not fully scanned" block has a row such as `9  folders or files not readable: access denied`.

**Explanation:** The operating system refused to let PIIDigger list those folders or open those files. Search the log for `access denied` to find each one. Each line is logged at WARNING and names the path, followed by the operating system's own message.

**Solutions:**

- **Windows:** run the scan from a terminal opened with **Run as administrator**. PIIDigger's startup admin check warns when it isn't. Windows also reports a file that another program has open, such as an Outlook `.pst` file, as access denied. Close that program and scan again.
- **Linux:** run the scan as a user that can read the folders, for example with `sudo`.
- **macOS:** the message is usually `Operation not permitted`. macOS privacy controls protect folders such as `~/Library/Mail`, `~/Library/Messages` and other apps' data, even from an administrator. Give the app you run PIIDigger from Full Disk Access:
    1. Open **System Settings › Privacy & Security › Full Disk Access**.
    2. Turn on the terminal app you use, such as Terminal or iTerm. Use **+** to add it if it isn't listed.
    3. Quit and reopen the terminal app, then run the scan again.

  The summary prints this pointer on macOS when at least one refusal was `Operation not permitted`. A message of `Permission denied` instead means ordinary file permissions, as on Linux, and Full Disk Access doesn't change it, so the summary leaves the pointer out when every refusal was that.

  PIIDigger also checks for this before the scan starts, and asks whether to continue when macOS refuses folders the scan would reach. See [macOS privacy prompt](scan-command.md#macos-privacy-prompt).

### The summary says files were password protected

**Symptoms:** The summary's "Not fully scanned" block has a row such as `4  files password protected, not scanned`.

**Explanation:** Those PDF, Word or Excel files are encrypted with a password, so their content can't be read. PIIDigger never asks for passwords. Search the log for `password protected` to find each one. Each line is logged at WARNING, as `skipping <path>: PDF is password protected` or the same for a Word document or Excel workbook.

**Solutions:** Such a file may still hold PII. Open it with its password and check it by hand, or save an unprotected copy and scan that.

### A scan appears hung

**Symptoms:** No progress-bar movement for a long time, and the log file shows nothing new.

**Solutions:** First, check the log for repeated timeout/termination messages — a single stuck file is handled automatically (see the "A worker seems stuck" entry in the [Scan Command Guide](scan-command.md#troubleshooting)) and shouldn't stall the whole run. If the whole process genuinely seems stuck, try **Ctrl+C** once and give it a few seconds. If that doesn't work, stop it at the OS level.

PIIDigger runs as several processes, one for each worker plus one that coordinates them. Their names depend on how you installed it:

| Install method | Windows | Linux/macOS |
|---|---|---|
| Standalone package from GitHub Releases | `python.exe` | `piidigger` |
| `uv tool install` | `piidigger.exe` and `python.exe` | `piidigger` and `python` |

On Windows, stop both names. This also stops any other Python programs you have running:

```powershell
# Windows PowerShell
Get-Process piidigger, python -ErrorAction SilentlyContinue | Stop-Process
```

```powershell
# Windows Task Manager
# Manually "End Task" on each piidigger.exe and python.exe process in the list
```

On Linux and macOS, match on the command line instead. Every PIIDigger process has `piidigger` somewhere in its command line, whichever way you installed it:

```bash
# Linux/macOS
pkill -f piidigger
```

Check that nothing is left with `pgrep -fl piidigger`. If a process is still listed, run `pkill -9 -f piidigger` to force it to stop.

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
