# Phase 5 Plan — ZIP Archive Support

**Branch**: `refactor`  
**Status**: Historical — implemented, then partially superseded. Decisions 1, 3, and 4 below (nested-archive deferral, lineage fields, log levels) shipped as written. **Decision 2 (binary member extraction via `open_bytes()`/`open_stream()` on `ArchiveMemberItem`) did not survive to the final implementation** — it was superseded by [ADR-multi-format-archives.md](./ADR-multi-format-archives.md)'s revision-3 unified `extract_member()` path, which deleted `ArchiveMemberItem` entirely in favor of `FilesystemItem`. The §10 checklist below still describes the pre-revision-3 approach in places and was never the authoritative progress tracker (see line below) — for current status use [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md), and for current design use [docs/architecture/archives/archive-handling.md](../architecture/archives/archive-handling.md).
**Last Updated**: 2026-07-06  
**Reference**: [ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md) (design; this document closes the open decisions and fills the gaps)

---

## 1. Purpose

This document closes the four open decisions from `ZIP_HANDLING_PLAN.md §13`, documents design constraints not stated there, and updates code touchpoints to reflect Phase 4's actual module layout.  The [IMPLEMENTATION_CHECKLIST.md Phase 5 section](./IMPLEMENTATION_CHECKLIST.md) tracks coding progress; this document is the pre-coding rationale record.

---

## 2. Closed Decisions

### Decision 1 — Nested ZIP scanning (milestone 1 scope)

**Decision: Defer nested archives to a follow-on milestone.**

Phase 5 implements `max_depth=1` only.  The `depth` field in payload models and the `max_depth` config field are wired in place for future use, but the `ENUM_ARCHIVE_MEMBERS` handler will not emit new `ENUM_ARCHIVE_MEMBERS` tasks for inner ZIP members in this milestone.  Inner ZIP members at depth 0 are treated as unrecognised binary and skipped with a debug log.

Rationale: milestone 1 is already substantial (new task types, new source, routing change, 8 test fixtures, safety controls).  Nesting doubles the coordinator fan-out complexity and requires coordinated depth tracking across re-entrant tasks.  The structural groundwork (depth field, `max_depth` config, `ArchiveMemberRef`) is already in the models; enabling recursion later requires only a one-line routing branch in the handler.

### Decision 2 — Binary member extraction approach

**Decision: In-memory for all handlers. `materialize()` is a documented last-resort fallback only.**

All three binary handler libraries were confirmed to support in-memory data natively:

| Handler | In-memory API |
|---|---|
| `docx2python` | `docx2python(BytesIO(bytes))` — typed as `str \| PathLike \| BytesIO` |
| `openpyxl` | `load_workbook(BytesIO(bytes))` — documented: "the path to open or a file-like object" |
| `xlrd` | `open_workbook(file_contents=bytes)` — named parameter, explicitly supported |

The `ScannableItem` protocol is extended with an `open_bytes() -> bytes | None` method (see §3c).  Binary handlers call `open_bytes()` first; if it returns `None` (the `FilesystemItem` case, to preserve read_only streaming on large on-disk files), they fall back to `materialize()`.  For `ArchiveMemberItem`, `open_bytes()` always returns the member content, so `materialize()` is never called in practice for the supported formats.

`materialize()` remains in the protocol and is implemented in `ArchiveMemberItem` (with best-effort secure deletion — see §4) as a correct fallback for any future handler that genuinely requires a file path.

### Decision 3 — Lineage fields: optional vs. mandatory

**Decision: Already closed by the existing code.**

`ResultRecord` ([models/results.py](../../src/piidigger/models/results.py)) already has `source_member_path`, `source_depth`, and `source_container_type` as optional fields defaulting to `None`/`0`.  Archive member results populate them; on-disk file results leave them at their defaults.  No change required.

### Decision 4 — Log level for unsupported archive members

**Decision: warning for members rejected by a safety rule; debug for members skipped because no handler exists.**

| Condition | Log level |
|---|---|
| Path traversal detected | WARNING |
| Encrypted member | WARNING |
| Exceeds size limit | WARNING |
| Exceeds member count | WARNING |
| Compression ratio > 1000× | WARNING |
| No registered handler for extension/MIME | DEBUG |
| Nested archive (depth limit) | DEBUG |

---

## 3. Design Constraints Not in the Original Plan

### 3a. `zipfile.ZipFile` is not picklable

A `ZipFile` object cannot cross the process boundary.  **`ArchiveMemberItem` must hold only serializable fields**: `archive_path: Path` and `member_path: str`.  It opens a fresh `ZipFile` on each call to `open_stream()`, `open_bytes()`, and `materialize()`.

```python
class ArchiveMemberItem:
    def __init__(self, archive_path: Path, member_path: str, ...) -> None:
        self._archive_path = archive_path   # Path — picklable
        self._member_path = member_path     # str  — picklable
        # Do NOT store zipfile.ZipFile as an attribute

    def open_bytes(self) -> bytes:
        with zipfile.ZipFile(self._archive_path, "r") as zf:
            return zf.read(self._member_path)   # fully in-memory, no temp file
```

`ScanArchiveMemberPayload` likewise carries only `archive_path: Path` and `member_path: str`.

### 3b. Routing change required in `handle_enum_dir`

The current `handle_enum_dir` ([worker/_enum_dir.py](../../src/piidigger/orchestration/worker/_enum_dir.py)) routes files via `get_handler_for(ext, mime)`.  `.zip` files have no registered `FileHandler`, so they are silently skipped today.

The routing change:

```
if config.archives.enabled AND ext in supported_archive_extensions:
    emit ENUM_ARCHIVE_MEMBERS task
else:
    if get_handler_for(ext, mime) is not None:
        emit SCAN_FILE task
```

`supported_archive_extensions` derives from `config.archives.formats` (e.g. `[".zip"]`).  This change is the only required modification to the directory-scan path — not to `coordinator.py` or `worker/_loop.py`.

### 3c. New `open_bytes()` method on `ScannableItem` protocol

Add to `protocols.py`:

```python
class ScannableItem(Protocol):
    ...
    def open_bytes(self) -> bytes | None: ...
```

Implementations:
- `FilesystemItem.open_bytes()` → returns `None`.  Signals "use `materialize()` for streaming".  This preserves openpyxl's `read_only=True` mode for large on-disk XLSX files.
- `ArchiveMemberItem.open_bytes()` → returns member bytes from the ZIP.  Always in-memory; bounded by `max_member_uncompressed_size_mb` (default 50 MB).

Handler pattern:

```python
def read(self, source) -> Iterator[str]:
    data = source.open_bytes()
    if data is not None:
        book = openpyxl.load_workbook(BytesIO(data), read_only=False, data_only=True)
    else:
        book = openpyxl.load_workbook(str(source.materialize()), read_only=True, data_only=True)
```

All three binary handlers (`docx`, `xlsx`, `xls`) are updated to follow this pattern.

### 3d. `SCAN_ARCHIVE_TEXT_CHUNK` task type — dropped

The existing `FileHandler.read(source) -> Iterator[str]` pattern handles chunking inside the handler.  No additional task type is needed.  The `SCAN_ARCHIVE_TEXT_CHUNK` entry from `ZIP_HANDLING_PLAN.md §5` is not implemented.

---

## 4. Secure Deletion

The original plan called for "overwrite before delete" to prevent PII leakage from extracted temp files.  The user has confirmed this best-effort approach should still be made, while accepting that on SSD hardware (where wear leveling prevents reliable block-level overwrite), physical data remnants may remain.  This limitation will be documented in the user guide.

**Decision: implement `secure_delete()` as a stdlib-only 2-pass overwrite.  No third-party library.**

```python
def secure_delete(path: Path) -> None:
    """Best-effort overwrite before delete.

    Performs two passes (zero fill, then random fill) before unlinking the file.
    On SSD hardware, physical data remnants may remain due to wear leveling —
    this is a hardware limitation that cannot be addressed in software.
    See docs/security-considerations.md.
    """
    try:
        size = path.stat().st_size
        with path.open("r+b") as f:
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
            f.seek(0)
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass  # best-effort; if overwrite fails, still unlink below
    path.unlink(missing_ok=True)
```

`secure_delete()` lives in `src/piidigger/orchestration/secure_delete.py` (its own module for easy future replacement).

Because all current binary handlers use `open_bytes()` (in-memory), `materialize()` is not called in normal operation.  `secure_delete()` is invoked by `_cleanup_temp_workspace()` for any temp files that do exist — guarding against future handlers, bugs, or unexpected fallback paths.

---

## 5. Temp Directory Isolation from `ENUM_DIR`

Any temp file created by `materialize()` must not be scanned by a worker running `ENUM_DIR`.

**Design**: create a single PIIDigger-owned temp root at scan startup; add it to `exclude_dirs` before the first task is enqueued.

```
run_scan():
    temp_base = Path(tempfile.mkdtemp(prefix="piidigger_"))
    config.exclude_dirs.append(str(temp_base))   # Config needs to be mutable here
    ctx = WorkerContext(..., temp_base=temp_base)
    ...
    # After coordinator exits:
    shutil.rmtree(temp_base, ignore_errors=True)
```

Workers create per-task temp directories under `temp_base`:

```python
task_temp = Path(tempfile.mkdtemp(dir=ctx.temp_base))
# pass task_temp to ArchiveMemberItem.materialize()
# _cleanup_temp_workspace() calls secure_delete() on contents, then rmdir
```

**WorkerContext changes**:
- Add `temp_base: Path` field to `WorkerContext` (frozen dataclass).  `Path` is picklable.

**Config mutability note**: `Config` is currently immutable (Pydantic model).  Adding `temp_base` to `exclude_dirs` at runtime requires either: (a) making `exclude_dirs` a field with `model_config = ConfigDict(frozen=False)` temporarily, or (b) constructing a new `Config` instance with the updated list.  Option (b) is cleaner — `run_scan()` builds a `runtime_config = config.model_copy(update={"exclude_dirs": [*config.exclude_dirs, str(temp_base)]})` and passes `runtime_config` to workers.

---

## 6. Progress Panel Layout

**Decision: no new progress bars.  Archive members fold into the existing Files and Bytes bars.**

Archive handlers emit the same counter keys as file handlers:

| Event | Counter key | Bar it drives |
|---|---|---|
| `ENUM_ARCHIVE_MEMBERS` emits a member task | `files_found` | Files bar (found) |
| `SCAN_ARCHIVE_MEMBER` completes | `files_scanned`, `bytes_scanned` | Files bar (scanned), Bytes bar |

`ProgressDisplay` requires no changes.  The existing bars accurately reflect total scan progress regardless of whether a file came from disk or from inside an archive.

`archive_members_skipped` and `archive_errors` are diagnostic counters only.  Handlers track them in `TaskResult.counters`; the coordinator accumulates them; the stop summary line reports them alongside `results_found`.

---

## 7. CLI Flags

The Phase 4 decision removed all worker-count CLI overrides in favour of the `performance` config key.  Archive settings follow the same philosophy: **all archive tuning lives in `piidigger.toml`**, with one exception.

**Decision: one CLI flag only — `--no-archives` / `--archives-enabled`.**

| Flag | Effect |
|---|---|
| `--no-archives` | Sets `config.archives.enabled = False` for this run, regardless of TOML |
| `--archives-enabled` | (default) honours the TOML setting |

All other `[archives]` TOML keys (`max_depth`, `max_members`, `max_member_uncompressed_size_mb`, `max_total_uncompressed_size_mb`, `task_timeout_seconds`) are config-file only.

---

## 8. Updated Code Touchpoints

These replace the stale §14 list in `ZIP_HANDLING_PLAN.md` (which referenced deleted modules).

| File | Change |
|---|---|
| `src/piidigger/protocols.py` | Add `open_bytes() -> bytes \| None` to `ScannableItem` protocol |
| `src/piidigger/orchestration/sources.py` | Add `ArchiveMemberItem`; update `FilesystemItem.open_bytes()` → `None` |
| `src/piidigger/orchestration/secure_delete.py` | New file — `secure_delete(path: Path) -> None` |
| `src/piidigger/orchestration/context.py` | Add `temp_base: Path` to `WorkerContext` |
| `src/piidigger/models/tasks.py` | Uncomment and add `ENUM_ARCHIVE_MEMBERS`, `SCAN_ARCHIVE_MEMBER` to `TaskType` |
| `src/piidigger/models/payloads.py` | Add `ArchiveOrigin`, `ArchiveMemberRef`, `EnumArchiveMembersPayload`, `ScanArchiveMemberPayload` |
| `src/piidigger/models/config.py` | Add `ArchiveConfig` nested model; add `archives: ArchiveConfig` to `Config`; update `_KNOWN_CONFIG_KEYS`; add `[archives]` block to `generate_toml_template()` |
| `src/piidigger/filehandlers/docx.py` | Use `open_bytes()` / BytesIO path; fall back to `materialize()` when `None` |
| `src/piidigger/filehandlers/xlsx.py` | Use `open_bytes()` / BytesIO path; fall back to `materialize()` + read_only when `None` |
| `src/piidigger/filehandlers/xls.py` | Use `open_bytes()` via `file_contents=` parameter; fall back to `materialize()` when `None` |
| `src/piidigger/orchestration/worker/_enum_dir.py` | Add archive routing branch |
| `src/piidigger/orchestration/worker/_enum_archive.py` | New file — `handle_enum_archive_members()` |
| `src/piidigger/orchestration/worker/_scan_archive_member.py` | New file — `handle_scan_archive_member()` |
| `src/piidigger/orchestration/worker/_loop.py` | Add both handlers to `DISPATCH`; extend `_cleanup_temp_workspace()` |
| `src/piidigger/orchestration/progress.py` | Update stop summary line to include `archive_members_skipped` and `archive_errors` |
| `src/piidigger/run.py` | Create `temp_base`; build `runtime_config` with exclusion; pass to `WorkerContext`; cleanup at shutdown |
| `src/piidigger/cli/commands/scan.py` | Add `--no-archives` / `--archives-enabled` flag |
| `tests/testdata/zip/` | 8 fixture files + `create_fixtures.py` |
| `tests/` | New test modules (see §10) |

`coordinator.py` and `worker/_loop.py` body: **zero changes to fan-out logic or the dispatch loop.**  The Phase 5 exit criterion verifies this with `git diff`.

---

## 9. Test Fixture Construction

Most fixtures can be created with `zipfile.ZipFile` in a `create_fixtures.py` helper script.  Two require special handling.

### `traversal-member.zip`
Python's `zipfile` module sanitises member names, stripping `..` components.  This fixture must be constructed by writing raw ZIP bytes: create a valid `ZipInfo` with `filename = "../traversal.txt"` and insert it into the central directory manually.  The `create_fixtures.py` script handles this.  The script is run once and its output committed; it is not a pytest fixture generator (avoids slow fixture generation on every test run).

### `zip-bomb-simulated.zip`
A real zip bomb is unsafe to store in the repo.  The fixture uses a single member whose *reported* uncompressed size in its `ZipInfo` header is large (e.g. 200 GB) but whose actual content is trivial (a few bytes).  Set `ZipInfo.file_size` manually in `create_fixtures.py`.  The compression ratio check fires on the reported size before extraction begins.

### Standard fixtures (use `zipfile` directly)
`simple-pii.zip`, `nested-depth-2.zip`, `oversize-member.zip`, `many-members.zip`, `encrypted-member.zip`, `corrupt.zip` can all be created with the standard library and are included in `create_fixtures.py` for reproducibility.  Commit the generated binaries.

---

## 10. Detailed Implementation Checklist (Phase 5 Addendum)

> **Not a live status tracker.** This checklist predates the revision-3 unified extraction path — several boxes below describe `open_bytes()`/`open_stream()`/`ArchiveMemberItem` mechanics that were superseded and never shipped in that form (see the status banner at the top of this document). The boxes are left unchecked/as originally written for historical fidelity rather than retrofitted to match what actually shipped. [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md) is the authoritative record of what was actually built.

This supplements the existing [IMPLEMENTATION_CHECKLIST.md Phase 5](./IMPLEMENTATION_CHECKLIST.md) section.  Items here are the gaps not captured there.

### Pre-work

- [ ] Write `tests/testdata/zip/create_fixtures.py` and generate all 8 fixture files; commit binaries
- [ ] Extend `_cleanup_temp_workspace()` in `worker/_loop.py` to: (a) delete per-task temp directory contents via `secure_delete()`; (b) run unconditionally (both `ok` and `error` paths)
- [ ] Verify `worker/_loop.py` `try/finally` placement is correct — temp cleanup runs even when the handler raises

### `secure_delete()` utility — `orchestration/secure_delete.py`

- [ ] Implement 2-pass overwrite (zeros + random) + `unlink()` as shown in §4
- [ ] Handle `OSError` in the overwrite pass gracefully — still attempt `unlink()`
- [ ] Unit test: file content is overwritten before deletion; file does not exist after call
- [ ] Unit test: `secure_delete()` on a non-existent path does not raise

### Temp dir isolation — `run.py` and `orchestration/context.py`

- [ ] Add `temp_base: Path` to `WorkerContext` frozen dataclass; document why `Path` is safe to pickle
- [ ] In `run_scan()`: create `temp_base = Path(tempfile.mkdtemp(prefix="piidigger_"))` before constructing `WorkerContext`
- [ ] Build `runtime_config = config.model_copy(update={"exclude_dirs": [*config.exclude_dirs, str(temp_base)]})` and pass to `WorkerContext`
- [ ] In `run_scan()` shutdown block: `shutil.rmtree(temp_base, ignore_errors=True)` (after coordinator exits)
- [ ] Unit test: `temp_base` path appears in the effective `exclude_dirs` that workers see
- [ ] Integration test: a file placed in `temp_base` during a scan is NOT picked up by `ENUM_DIR`

### `open_bytes()` protocol method — `protocols.py` and `sources.py`

- [ ] Add `open_bytes(self) -> bytes | None` to `ScannableItem` Protocol
- [ ] `FilesystemItem.open_bytes()` → `return None`
- [ ] `ArchiveMemberItem.open_bytes()` → opens fresh `ZipFile`, calls `zf.read(member_path)`, returns bytes
- [ ] Update `isinstance(item, ScannableItem)` protocol satisfaction tests to include `open_bytes()`

### Binary handler updates — `filehandlers/docx.py`, `xlsx.py`, `xls.py`

- [ ] `docx.py`: call `source.open_bytes()`; if not `None`, pass `BytesIO(data)` to `docx2python`; otherwise `docx2python(str(source.materialize()))`
- [ ] `xlsx.py`: call `source.open_bytes()`; if not `None`, `load_workbook(BytesIO(data), read_only=False)`; otherwise `load_workbook(str(source.materialize()), read_only=True)`
- [ ] `xls.py`: call `source.open_bytes()`; if not `None`, `xlrd.open_workbook(file_contents=data)`; otherwise `xlrd.open_workbook(str(source.materialize()))`
- [ ] Update handler docstrings to reflect new in-memory path
- [ ] Unit test each handler with a real fixture file passed as `FilesystemItem` (existing behaviour preserved)
- [ ] Unit test each handler with bytes passed via a mock `open_bytes()` returning real file content (new archive path)

### `ArchiveConfig` model — `models/config.py`

- [ ] Add `ArchiveConfig(PiiDiggerModel)` with all fields from `ZIP_HANDLING_PLAN.md §10`
- [ ] Add `archives: ArchiveConfig = Field(default_factory=ArchiveConfig)` to `Config`
- [ ] Add `"archives.enabled"`, `"archives.max_depth"`, `"archives.formats"` etc. to `_KNOWN_CONFIG_KEYS`
- [ ] Add `[archives]` block to `generate_toml_template()` output
- [ ] Unit test: `Config.from_toml()` round-trips `[archives]` section correctly
- [ ] Unit test: unknown key inside `[archives]` produces a helpful error message (uses fuzzy suggestion)

### `ArchiveMemberItem` — `orchestration/sources.py`

- [ ] Implement with `_archive_path: Path` and `_member_path: str` only (no stored `ZipFile`)
- [ ] `open_stream()` opens a fresh `ZipFile` and returns the member stream; caller owns `close()`
- [ ] `open_bytes()` opens a fresh `ZipFile`, reads member fully into bytes, closes `ZipFile`
- [ ] `materialize()` extracts member to `tempfile.mkdtemp(dir=ctx.temp_base)` subdirectory; stores the extracted path for `_cleanup_temp_workspace()`; uses `secure_delete()` — see §4
- [ ] `mime` property: sniff from first few bytes via `open_stream()` if puremagic is available, else `None`
- [ ] `isinstance(item, ScannableItem)` protocol check passes
- [ ] Unit test: `open_bytes()` returns correct bytes for a known member
- [ ] Unit test: `open_stream()` yields correct bytes for a known member
- [ ] Unit test: `materialize()` creates a temp file; content matches; path is inside `temp_base`

### Routing change in `handle_enum_dir` — `worker/_enum_dir.py`

- [ ] Add helper `_is_archive_format(ext: str, config: Config) -> bool` — returns `True` when `config.archives.enabled` and `ext` is in the configured formats list
- [ ] In the file-routing block, check `_is_archive_format` before `get_handler_for` — emit `ENUM_ARCHIVE_MEMBERS` task and `continue` when True
- [ ] Update `counters` return: include `archives_found` key when any archive tasks are emitted
- [ ] Unit test: directory with `.zip` file and `archives.enabled=True` → emits `ENUM_ARCHIVE_MEMBERS`, not `SCAN_FILE`
- [ ] Unit test: same directory with `archives.enabled=False` → `.zip` file is skipped

### `handle_enum_archive_members` — `worker/_enum_archive.py`

- [ ] Validate payload as `EnumArchiveMembersPayload`
- [ ] Open archive; reject on `BadZipFile` — log WARNING, return `status="error"`
- [ ] Per-member safety checks in order (all rejections logged at WARNING):
  - [ ] Path traversal (`../`, absolute path)
  - [ ] Encrypted flag
  - [ ] `uncompressed_size > max_member_uncompressed_size_mb × 1024²`
  - [ ] Compression ratio `> 1000×` (when `compressed_size > 0`)
  - [ ] Running total uncompressed size exceeds `max_total_uncompressed_size_mb`
  - [ ] Member count exceeds `max_members`
- [ ] Nested ZIP detection: member extension is `.zip` → log DEBUG, increment `archive_members_skipped`, skip
- [ ] Extension/MIME check via `get_handler_for(ext, mime)` — if no handler, log DEBUG, increment `archive_members_skipped`, skip (consistent with `handle_enum_dir` filtering before emitting a task)
- [ ] Return `new_tasks`: one `SCAN_ARCHIVE_MEMBER` per accepted member
- [ ] Return `counters`: `files_found` (one per accepted member task emitted), `archive_members_skipped`, `archive_errors` — `files_found` drives the existing Files progress bar
- [ ] Unit test per safety rejection rule (7 tests)

### `handle_scan_archive_member` — `worker/_scan_archive_member.py`

- [ ] Validate payload as `ScanArchiveMemberPayload`; construct `ArchiveMemberItem`
- [ ] Look up handler via `get_handler_for(ext, mime)` — handler is guaranteed to exist (filtered at enum time); treat a miss as an `status="error"` and log a warning (defensive only)
- [ ] File handler `read()` loop → data handler `find_matches()` loop — same pattern as `handle_scan_file`
- [ ] Build `ResultRecord` with lineage fields populated: `source_path`, `source_member_path`, `source_depth`, `source_container_type="zip"`
- [ ] Return `counters`: `files_scanned: 1`, `bytes_scanned: N` — drives the existing Files and Bytes progress bars
- [ ] `try/finally`: delegate all temp file cleanup to `_cleanup_temp_workspace()`

### `DISPATCH` and `_cleanup_temp_workspace` — `worker/_loop.py`

- [ ] Add `TaskType.ENUM_ARCHIVE_MEMBERS: handle_enum_archive_members` to `DISPATCH`
- [ ] Add `TaskType.SCAN_ARCHIVE_MEMBER: handle_scan_archive_member` to `DISPATCH`
- [ ] Extend `_cleanup_temp_workspace()`: call `secure_delete()` on each file inside `task_temp`; then `task_temp.rmdir()`

### `ProgressDisplay` — `orchestration/progress.py`

- [ ] Add `archive_members_skipped` and `archive_errors` to the stop summary line (no new bars; no constructor change)
- [ ] Verify `_COUNTER_KEYS` already accumulates `files_found`, `files_scanned`, `bytes_scanned` — no change needed since archive handlers reuse these keys

### CLI — `cli/commands/scan.py`

- [ ] Add `--no-archives` / `--archives-enabled` flag; overrides `config.archives.enabled` before `run_scan()`
- [ ] Unit test: `--no-archives` disables archive scanning regardless of TOML setting

### Exit criterion proof

- [ ] `git diff HEAD~1 -- src/piidigger/orchestration/coordinator.py src/piidigger/orchestration/worker/_loop.py` — confirm zero diff to fan-out logic and dispatch
- [ ] Add assertion test: `test_phase5.py::test_coordinator_and_worker_loop_unchanged_vs_phase4`

---

## 11. Phase 5 Exit Criteria (complete set)

These supersede and expand on the checklist version.

- [ ] ZIP enumeration and member scanning run under the task queue architecture
- [ ] Zero changes to `coordinator.py` fan-out logic or `worker/_loop.py` dispatch loop vs. Phase 4 — verified by `git diff` assertion in `test_phase5.py`
- [ ] All safety limits (7 rejection rules) active and verified by independent unit tests
- [ ] Findings from archive members include all lineage fields; non-archive results are unchanged
- [ ] Binary handlers use `open_bytes()` in-memory path for archive members; `FilesystemItem` behavior unchanged
- [ ] `materialize()` on `ArchiveMemberItem` uses `secure_delete()` for cleanup; SSD limitation documented
- [ ] Temp base directory created at startup; added to `exclude_dirs`; cleaned up at shutdown
- [ ] `--no-archives` flag disables archive scanning
- [ ] Archive members accumulate in the existing Files and Bytes progress bars with no `ProgressDisplay` changes
- [ ] All 8 test fixtures exist; `create_fixtures.py` committed alongside them
- [ ] Coverage ≥ 80% maintained; `ruff` + `mypy` clean
