# Breaking Changes

This file documents changes that require action from existing PIIDigger users when upgrading.

---

## 2.0.0 (in development — `refactor` branch)

### 1. Configuration file format (TOML)

The `piidigger.toml` format has been redesigned. **Existing 1.x config files will not load.** PIIDigger will exit with a clear error message pointing to this file.

Run `piidigger config generate` to produce a valid 2.0 config file, then migrate your customizations.

#### Key renames (camelCase → snake_case)

| 1.x key | 2.0 key |
|---|---|
| `dataHandlers` | `data_handlers` |
| `localFilesOnly` | `local_files_only` |
| `[includeFiles]` | `[include_files]` |
| `[includeFiles.startDirs]` | `[include_files.start_dirs]` |
| `[excludeDirs]` | `[exclude_dirs]` |
| `[logging] logLevel` | `[logging] log_level` |
| `[logging] logFile` | `[logging] log_file` |
| `maxProcs` | `max_workers` (now optional; defaults to `cpu_count()`) |
| `[results] csv = true`, `json = true`, `text = true` | `[results] formats = ["csv", "json", "text"]` |

#### macOS key renamed

The OS key for macOS in `start_dirs` and `exclude_dirs` sections changes from `darwin` to `macos`.

```toml
# 1.x
[includeFiles.startDirs]
darwin = ["/"]

# 2.0
[include_files.start_dirs]
macos = ["/"]
```

#### List fields are always lists

Fields that accepted either a bare string or a list in 1.x now require a list in all cases. A bare string is rejected with a validation error at startup.

```toml
# 1.x — both of these were accepted
ext = "all"
ext = [".txt", ".pdf"]

# 2.0 — only list form accepted
ext = ["all"]
ext = [".txt", ".pdf"]
```

Affected fields: `data_handlers`, `include_files.ext`, `include_files.mime`, `include_files.start_dirs.*`, `exclude_dirs.*`.

#### Full 2.0 config reference

```toml
data_handlers           = ["pan", "email"]   # or ["all"]
local_files_only        = true
max_workers             = 4                  # optional; omit to use cpu_count()
default_timeout_seconds = 30                 # optional

[include_files]
ext  = ["all"]   # or e.g. [".txt", ".pdf", ".docx"]
mime = ["all"]   # or e.g. ["text/plain", "application/pdf"]

[include_files.start_dirs]
windows = ["all"]   # or ["all"] to scan all drive letters
linux   = ["/"]
macos   = ["/"]

[exclude_dirs]
windows = ["C:\\Windows", "C:\\Program Files (x86)", "C:\\Program Files"]
linux   = ["/boot", "/dev", "/etc", "/proc", "/run", "/snap", "/sys",
           "/usr/bin", "/usr/lib", "/usr/local", "/usr/sbin", "/usr/share"]
macos   = ["/dev", "/etc", "/usr/bin", "/usr/local/Homebrew",
           "/usr/lib", "/usr/sbin", "/Applications", "/System"]

[results]
path = "piidigger-results/"
formats = ["csv", "json", "text"]

[logging]
log_level = "WARNING"
log_file  = "logs/piidigger.log"
```

---

### 2. JSON output format

The `json = true` output setting now produces **two files** with matching timestamps instead of one.

| File | Description |
|---|---|
| `hostname-TIMESTAMP.jsonl` | JSON Lines — one JSON object per line, written as each result is found. Valid partial output on hard kill. |
| `hostname-TIMESTAMP.json` | Standard JSON array — identical content, written at scan completion. Suitable for `json.load()`, Excel importers, and tools that expect a JSON array. |

The JSON array content is equivalent to the 1.x output. The extra `.jsonl` file is new.

**Why two files:** the `.jsonl` file is written incrementally so a long-running scan never loses results to a crash. The `.json` array is produced from it at clean shutdown (including Ctrl+C). Only a hard process kill (`kill -9`, power loss) results in `.jsonl` without `.json`.

---

### 3. Output schema — new lineage fields

All output formats (CSV, JSON, JSONL, text) include new lineage fields introduced for archive-member support (e.g. ZIP files). For on-disk files these fields are always null/empty but are present in the schema from 2.0 onward.

New fields:

| Field | Type | Description |
|---|---|---|
| `source_member_path` | `string \| null` | Path of the member within an archive. `null` for on-disk files. |
| `source_depth` | `integer` | Nesting depth. `0` for on-disk files, ≥1 for archive members. |
| `source_container_type` | `string \| null` | Archive format (e.g. `"zip"`). `null` for on-disk files. |

Tools that consume PIIDigger output by column position (not by name) may be affected. Update column-index references in any downstream scripts.

---

### 4. CLI subcommand structure

`piidigger` now exposes subcommands. The default behavior (running a scan) is unchanged when called without arguments.

| 1.x invocation | 2.0 equivalent |
|---|---|
| `piidigger` | `piidigger scan` (or just `piidigger` — scan is the default) |
| `piidigger --config FILE` | `piidigger scan --config FILE` |
| `piidigger --create-conf FILE` | `piidigger config generate` — write a default config file |
| `*(not available)*` | `piidigger config validate FILE` — validate a config file |

All existing scan flags (`--start-dirs`, `--log-level`, `--max-workers`, `--local-files-only`) are preserved under `piidigger scan`.

---

### Not breaking

The following are **unchanged** between 1.x and 2.0:

- PII detection logic (same handlers: pan, email)
- CSV output format and column names (except new lineage columns — see above)
- Text output format
- Supported file types and their handling (plaintext, PDF, DOCX, XLSX, XLS)
- `--help` behavior at the top level
