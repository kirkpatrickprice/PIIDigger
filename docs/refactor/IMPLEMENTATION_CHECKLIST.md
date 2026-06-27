# Implementation Checklist

**Branch**: `refactor`
**Status**: Phase 3 Waves 1–3 complete — Phase 3 tests + exit criteria remaining
**Last Updated**: 2026-06-27
**Reference**: [ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md)

Use this checklist to track progress. Mark items `[x]` as completed. Each phase ends with an exit-criteria gate — do not start the next phase until all exit criteria are met.

---

## Phase 0 — Standards & Scaffolding

*No behavior changes. Sets the baseline that all new code is born into.*

### snake_case rename — retained business logic

- [ ] Run `ruff check --select N src/` to enumerate all naming violations
- [ ] `datahandlers/`: rename all functions, variables, and module-level names to `snake_case`
  - [ ] `pan.py`
  - [ ] `email.py`
  - [ ] `phonenum.py`
  - [ ] `trackdata.py`
  - [ ] `__init__.py` exports
- [ ] `filehandlers/`: rename all functions, variables, and module-level names to `snake_case`
  - [ ] `plaintext.py`
  - [ ] `pdf.py`
  - [ ] `docx.py`
  - [ ] `xlsx.py`
  - [ ] `xls.py`
  - [ ] `_sharedfuncs.py`
  - [ ] `__init__.py` exports
- [ ] `outputhandlers/`: rename all functions, variables, and module-level names to `snake_case`
  - [ ] `csv.py`
  - [ ] `json.py`
  - [ ] `text.py`
  - [ ] `__init__.py` exports
- [ ] `console.py`: verify already snake_case (it was rewritten in a prior commit); fix any remaining violations
- [ ] `getencoding.py`, `getmime.py`: rename as needed
- [ ] `globalfuncs.py`: rename all functions and variables to `snake_case`
- [ ] `globalvars.py`: rename variables to `snake_case`; constants to `UPPER_CASE`

### Linting — enable `N` ruleset and reach zero violations

- [ ] Add `"N"` to `ruff` `select` list in `pyproject.toml`
- [ ] Run `ruff check src/ --select N` and fix all violations
- [ ] Run full `ruff check src/` (all enabled rulesets) — zero violations
- [ ] Run `mypy src/` — zero errors (or confirm existing suppressions are documented)
- [ ] Confirm CI passes (`ruff` + `mypy` gates both green)

### Extract `run_scan()` — make the core testable

- [ ] Create `src/piidigger/run.py` with a `run_scan(config: Config) -> int` function
  - [ ] Move the scan orchestration body out of the Click `main()` into `run_scan()`
  - [ ] `main()` in `piidigger.py` becomes: load config → call `run_scan(config)` → `sys.exit(code)`
  - [ ] `run_scan` must be importable and callable in a test without invoking Click

### Fix `errorCodes` bugs

- [ ] `classes.py:66`: replace `globalfuncs.errorCodes['invalidConfig']` with `globalvars.errorCodes['invalidConfig']`
- [ ] `classes.py:110`: same fix
- [ ] `piidigger.py:288`: replace non-existent key `errorCodes['unknown']` with `errorCodes['unknownError']`
- [ ] Add a test that exercises both invalid-config paths and verifies clean exit (no `AttributeError`)

### Scaffold new module stubs

Create empty-but-importable stubs (docstring + `__all__ = []` or equivalent) so import paths are established before Phase 1 fills them in:

- [ ] `src/piidigger/cli/__init__.py`
- [ ] `src/piidigger/cli/main.py` (empty `click.group()`)
- [ ] `src/piidigger/cli/commands/__init__.py`
- [ ] `src/piidigger/cli/commands/scan.py`
- [ ] `src/piidigger/cli/commands/config.py`
- [ ] `src/piidigger/models/__init__.py`
- [ ] `src/piidigger/models/config.py`
- [ ] `src/piidigger/models/tasks.py`
- [ ] `src/piidigger/models/payloads.py`
- [ ] `src/piidigger/models/results.py`
- [ ] `src/piidigger/protocols.py`
- [ ] `src/piidigger/orchestration/__init__.py`
- [ ] `src/piidigger/orchestration/context.py`
- [ ] `src/piidigger/orchestration/worker.py`
- [ ] `src/piidigger/orchestration/coordinator.py`
- [ ] `src/piidigger/orchestration/logging_setup.py`
- [ ] `src/piidigger/orchestration/progress.py`
- [ ] `src/piidigger/orchestration/sources.py`
- [ ] `src/piidigger/run.py`

### Phase 0 — Exit Criteria

- [ ] `ruff check src/` — zero violations (all rulesets including `N`)
- [ ] `mypy src/` — zero errors
- [ ] Existing test suite passes unchanged
- [ ] `run_scan(config)` is callable in a test without Click
- [ ] All module stubs importable without error

---

## Phase 1 — Core Infrastructure

*Orchestration machinery with a trivial no-op task. No business logic attached yet. Goal: prove pool start/dispatch/result/shutdown works correctly on Windows `spawn`.*

### Task model — `src/piidigger/models/tasks.py`

- [x] `TaskType` enum: `ENUM_DIR`, `SCAN_FILE`, `NOOP` (testing only); archive types reserved as comments
- [x] `Task` Pydantic model (frozen): `task_id`, `task_type`, `payload`, `timeout_seconds`
  - [x] `task_id` defaults to `uuid4().hex`
  - [x] `timeout_seconds` validated: `ge=1, le=600`
  - [x] Picklable: verified by round-trip test
- [x] `TaskResult` Pydantic model: `task_id`, `task_type`, `status`, `new_tasks`, `findings`, `counters`, `error_message`, `duration_seconds`, `worker_pid`
  - [x] `status` constrained to `Literal["ok", "timeout", "error"]`
  - [x] `duration_seconds` validated: `ge=0.0`
- [x] `TaskStarted` dataclass: `task_id`, `worker_pid` (heartbeat message — not a `TaskResult`)
- [x] `SHUTDOWN` sentinel constant (module-level; replaces 1.x `SENTINEL` string)

### WorkerContext — `src/piidigger/orchestration/context.py`

- [x] `WorkerContext` frozen dataclass: `config`, `task_queue`, `result_queue`, `log_queue`, `stop_event`
- [x] Docstring states: why `dataclass` not Pydantic; what is and is not allowed (no `Logger`, no `Console`)
- [x] Verified picklable across `mp.spawn` on Windows

### Logging setup — `src/piidigger/orchestration/logging_setup.py`

- [x] `build_worker_logger(log_queue, name) -> logging.Logger`: attaches `QueueHandler`; called inside each process
- [x] `start_listener(log_queue, log_file, log_level) -> QueueListener`: creates `FileHandler`, starts listener thread
- [x] `stop_listener(listener)`: stops the listener; called after all workers have joined
- [x] Verified: log records written inside a worker process appear in the log file

### Worker — `src/piidigger/orchestration/worker.py`

- [x] `DISPATCH` dict: maps `TaskType` → handler callable; initially contains only `NOOP` handler
- [x] `worker_loop(ctx: WorkerContext) -> None`:
  - [x] Builds its own logger via `build_worker_logger()`
  - [x] Loop: `task_queue.get()` → check `SHUTDOWN` → put `TaskStarted` heartbeat → `_dispatch()` → `try/finally _cleanup_temp_workspace()` → put `TaskResult`
  - [x] `_dispatch()` wraps handler in `try/except`; exceptions become `status="error"` results, never propagate
  - [x] `_cleanup_temp_workspace()`: no-op in Phase 1; stub with documented interface
  - [x] Handles `KeyboardInterrupt` cleanly: finishes current task, exits loop
- [x] `_handle_noop(task, ctx, logger) -> TaskResult`: returns `status="ok"` instantly; used for integration testing only

### Worker pool helpers — `src/piidigger/orchestration/worker.py`

- [x] `start_worker_pool(ctx, n_workers) -> list[mp.Process]`: spawns N `worker_loop` processes
- [x] `broadcast_shutdown(task_queue, n_workers)`: puts N `SHUTDOWN` sentinels
- [x] `join_workers(workers, timeout)`: joins all; force-terminates any still alive after timeout; logs each outcome

### Phase 1 — Tests

- [x] Unit: `Task` creation with valid fields
- [x] Unit: `Task` rejects invalid `timeout_seconds` (out of range)
- [x] Unit: `Task` rejects unknown `task_type`
- [x] Unit: `Task` pickles and unpickles cleanly (`pickle.dumps` / `pickle.loads`)
- [x] Unit: `TaskResult` rejects invalid `status` literal
- [x] Unit: `build_worker_logger()` returns a logger that puts records on `log_queue`
- [x] Integration: start pool of 2 workers, dispatch 10 `NOOP` tasks, collect all 10 results, shut down cleanly
- [x] Integration: log records from workers appear in log file after `stop_listener()`
- [x] Unit: `Config` (WorkerContext custom payload) pickles cleanly; NOOP pool integration test proves full spawn-boundary transit

### Phase 1 — Exit Criteria

- [x] Pool start → dispatch → result → shutdown cycle works on Windows `spawn`
- [x] Worker log records reach the file handler
- [x] All unit and integration tests pass
- [x] `ruff check` + `mypy` clean on all Phase 1 files

---

## Phase 2 — Coordinator & Control Flow

*The riskiest piece: the fan-out loop, `pending` termination logic, deadline monitoring, and graceful shutdown. No real scan logic — business handlers are stubs.*

### Coordinator — `src/piidigger/orchestration/coordinator.py`

- [x] `run_coordinator(ctx, workers, listener, sinks, progress) -> None`:
  - [x] Seeds initial tasks: one `ENUM_DIR` per `config.start_dirs`; sets `pending = len(start_dirs)`
  - [x] Main loop: `result_queue.get(timeout=HEARTBEAT_CHECK_INTERVAL)`
    - [x] On `queue.Empty`: call `_check_worker_deadlines()`; continue
    - [x] On `TaskStarted`: call `_record_heartbeat()`; continue (does not change `pending`)
    - [x] On `TaskResult`: `pending -= 1`; enqueue each `new_task` (`pending += 1` per); route `findings`; call `progress.update(counters)`
  - [x] Termination: when `pending == 0`, exits loop
  - [x] Post-loop: `broadcast_shutdown()` → `join_workers()` → `_flush_sinks()` → `stop_listener()`
- [x] `_record_heartbeat(msg)`: stores `{task_id: (worker_pid, timestamp)}`
- [x] `_check_worker_deadlines()`:
  - [x] For each in-flight task older than `2 × timeout_seconds`: log warning; `terminate()` the owning worker; spawn replacement; synthesize `status="timeout"` `TaskResult`; decrement `pending`
  - [x] Phase 4 will add crash-before-heartbeat re-queue; stub the extension point here
- [x] `KeyboardInterrupt` handler: calls `broadcast_shutdown()`, joins workers with timeout, flushes sinks, exits (via `try/finally`)

### Progress display — `src/piidigger/orchestration/progress.py`

- [x] `ProgressDisplay` class, owned by the coordinator, constructed before the main loop
- [x] `start() -> None`: opens `rich.Live` context with two-panel `rich.Layout`
  - [x] Top panel: `rich.Progress` with tasks for `dirs_found`, `dirs_scanned`, `files_found`, `files_scanned`, `bytes_scanned`, `results_found`
  - [x] Bottom panel: fixed-height scrolling events log (`rich.Table`, circular buffer of last N lines)
- [x] `update(counters: dict[str, int]) -> None`: increments the relevant progress bars
- [x] `log_event(level: str, message: str) -> None`: prepends a line to the events panel (with timestamp and colour by level)
- [x] `stop() -> None`: closes `rich.Live`; prints final summary line to stdout
- [x] Non-TTY mode: when `rich.Console` is not a terminal, `start()`/`update()`/`log_event()` are no-ops; `stop()` prints plain-text summary

### Simulated end-to-end test chain (Phase 2 integration)

- [x] Add `ENUM_DIR` and `SCAN_FILE` stub handlers to `DISPATCH` (return synthetic `new_tasks` / `counters` without touching the filesystem):
  - [x] `ENUM_DIR` stub: returns 2 child `ENUM_DIR` tasks + 3 `SCAN_FILE` tasks (fixed synthetic payloads)
  - [x] `SCAN_FILE` stub: returns `counters={"files_scanned": 1, "bytes_scanned": 1024}`
- [x] Run coordinator with stub handlers on a synthetic tree; verify `pending` reaches 0 and loop exits

### Phase 2 — Tests

- [x] Unit: coordinator seeds correct number of initial tasks for N start dirs
- [x] Unit: `pending` counter arithmetic — decrement + re-increment stays consistent across one result with 3 `new_tasks`
- [x] Unit: `_check_worker_deadlines()` synthesizes a timeout result and decrements `pending` for an expired task
- [x] Unit: `ProgressDisplay.update()` increments counters correctly
- [x] Unit: `ProgressDisplay` no-ops cleanly when not a TTY
- [x] Integration: coordinator + stub handlers — full fan-out loop reaches `pending == 0` on a synthetic tree of depth 2
- [x] Integration: `Ctrl+C` during fan-out exits within 5 seconds, no orphan processes remain — **skipped on Windows** (cross-process SIGINT requires `CREATE_NEW_PROCESS_GROUP`; tested on POSIX only)
- [x] Integration: a deliberately hung stub handler triggers deadline detection, is terminated, and scan continues

### Phase 2 — Exit Criteria

- [x] Coordinator reaches `pending == 0` correctly on a synthetic tree
- [x] `Ctrl+C` exits cleanly within 5 seconds (POSIX verified; Windows skip documented)
- [x] Deadline detection terminates a hung worker and the scan continues
- [x] Progress display renders without error in TTY mode; is silent in non-TTY mode
- [x] All tests pass; `ruff` + `mypy` clean

---

## Phase 3 — Re-contract & Wire Business Logic

*First real scan. Business logic re-contracted under the new protocols and attached to the orchestration layer. End-to-end directory scan must produce correct output.*

### Protocols — `src/piidigger/protocols.py`

- [x] `DataHandler(Protocol)`: `name: str`; `find_matches(text: str) -> dict[str, set[str]]`
- [x] `FileHandler(Protocol)`: `read(source: ScannableItem) -> Iterator[str]`
- [x] `OutputSink(Protocol)`: `open() -> None`; `write(record: ResultRecord) -> None`; `close() -> None`
- [x] `ScannableItem(Protocol)`: `display_path: str`; `ext: str`; `mime: str | None`; `size: int`; `depth: int`; `open_stream() -> IO[bytes]`; `materialize() -> Path`

### Sources — `src/piidigger/orchestration/sources.py`

- [x] `FilesystemItem(ScannableItem)`: wraps `pathlib.Path`
  - [x] `display_path`: `str(path)`
  - [x] `ext`: `path.suffix`
  - [x] `mime`: result of MIME detection (or `None` if detection disabled)
  - [x] `size`: `path.stat().st_size`
  - [x] `depth`: always `0`
  - [x] `open_stream()`: `open(path, "rb")`
  - [x] `materialize()`: returns `path` itself (no copy)
- [x] `FilesystemItem` passes the `ScannableItem` Protocol check (`typing.runtime_checkable` or `isinstance` test)

### Configuration model — `src/piidigger/models/config.py`

- [x] `Config` Pydantic model replaces the 1.x `classes.Config` getter-soup
  - [x] `start_dirs: list[Path]`
  - [x] `exclude_dirs: list[str]`
  - [x] `include_exts: list[str]`
  - [x] `include_mime: list[str]`
  - [x] `data_handlers: list[str]`
  - [x] `max_workers: int` (defaults to `cpu_count()`)
  - [x] `default_timeout_seconds: int` (default: 30)
  - [x] `local_files_only: bool`
  - [x] `log_file: Path`
  - [x] `log_level: str`
  - [x] Nested `results: ResultsConfig` (output file paths per format)
  - [x] `@classmethod from_toml(path: Path) -> Config`: loads and validates; raises with clear message on invalid TOML or missing required fields
  - [x] `@classmethod default() -> Config`: returns built-in defaults
  - [ ] Validation: start dirs must exist; log dir must be creatable; `data_handlers` must be known names

### Result model — `src/piidigger/models/results.py`

- [x] `ResultRecord` Pydantic model (as specified in architecture doc, with all lineage fields)
- [x] `source_member_path`, `source_depth`, `source_container_type` present but optional/defaulted; non-null for archive members

### Payload models — `src/piidigger/models/payloads.py`

- [x] `EnumDirPayload(BaseModel)`: `path: Path`; `depth: int = 0`
- [x] `ScanFilePayload(BaseModel)`: `display_path: str`; `file_path: Path`; `ext: str`; `mime: str | None`; `size: int`; `depth: int = 0`

### Real task handlers — `src/piidigger/orchestration/worker.py`

- [x] `handle_enum_dir(task, ctx, logger) -> TaskResult`:
  - [x] Validates payload as `EnumDirPayload`
  - [x] Iterates directory; respects `config.exclude_dirs`, `config.local_files_only`
  - [x] Handles `PermissionError`, `OSError`, `FileNotFoundError` — logs and continues
  - [x] Returns `new_tasks`: one `ENUM_DIR` per subdirectory, one `SCAN_FILE` per matching file
  - [x] Returns `counters`: `{"dirs_scanned": 1, "dirs_found": N, "files_found": M, "bytes_found": B}`
- [x] `handle_scan_file(task, ctx, logger) -> TaskResult`:
  - [x] Validates payload as `ScanFilePayload`
  - [x] Constructs a `FilesystemItem`
  - [x] Loads enabled `FileHandler` for the item's extension/MIME
  - [x] Iterates chunks from `file_handler.read(item)`
  - [x] For each chunk, runs all enabled `DataHandler.find_matches()` instances
  - [x] Aggregates matches into `ResultRecord` entries
  - [x] Returns `findings` (list of serialized `ResultRecord`) and `counters`: `{"files_scanned": 1, "bytes_scanned": N}`
  - [x] Handles unreadable files, encoding errors — logs and returns `status="error"`
- [x] Update `DISPATCH` table: replace stubs with real handlers

### Data handlers — re-contracted

- [x] Each handler implements `DataHandler` protocol
- [x] Function renamed: `find_match` → `find_matches` (returns `dict[str, set[str]]`)
- [x] No imports of `multiprocessing`, queues, or loggers
- [x] Unit tests pass without changes (just import path updates)

### File handlers — re-contracted

- [x] Each handler implements `FileHandler` protocol: `read(source: ScannableItem) -> Iterator[str]`
- [x] Text-based handlers (`plaintext`, `pdf`): use `source.open_stream()` with encoding detection
- [x] Binary handlers requiring a real path (`docx`, `xlsx`, `xls`): call `source.materialize()`; documented in class docstring
- [x] Handlers do not import `multiprocessing`, queues, or loggers

### Output sinks — re-implemented

- [x] `outputhandlers/csv.py`: `CsvSink(OutputSink)` — `open()` creates file + writes header; `write(record)` appends row; `close()` flushes
- [x] `outputhandlers/json.py`: `JsonSink(OutputSink)` — `open()` opens file; `write(record)` appends JSON line; `close()` finalizes
- [x] `outputhandlers/text.py`: `TextSink(OutputSink)` — `open()` opens file; `write(record)` appends formatted line; `close()` flushes
- [x] All sinks write `ResultRecord` lineage fields (even if null for on-disk files)
- [x] All sinks handle `IOError` gracefully — log, do not crash the coordinator

### CLI scaffolding — `src/piidigger/cli/`

- [x] `cli/main.py`: `@click.group()` with `scan` as default command
- [x] `cli/commands/scan.py`: all current Click options migrated from `piidigger.py`; calls `run_scan(config)`
- [x] `cli/commands/config.py`: `generate` subcommand (write default TOML); `validate` subcommand (load and report errors)
- [x] `run.py`: `run_scan(config: Config) -> int` wires together: start logging listener → start workers → run coordinator → return exit code

### Phase 3 — Tests

- [ ] Unit: `FilesystemItem` satisfies `ScannableItem` protocol
- [ ] Unit: `Config.from_toml()` loads a valid TOML; rejects invalid with clear message
- [ ] Unit: `Config.from_toml()` with missing start dir raises with message (not `AttributeError`)
- [ ] Unit: each `DataHandler` — `find_matches()` returns correct type on known input
- [ ] Unit: each `FileHandler` — `read()` yields non-empty strings from a test fixture file
- [ ] Unit: each `OutputSink` — `open()`/`write()`/`close()` produces correct file content with `tmp_path`
- [ ] Unit: `handle_enum_dir()` with a real temp directory returns correct `new_tasks` and `counters`
- [ ] Unit: `handle_scan_file()` with a test fixture returns correct `findings`
- [ ] Unit: `handle_scan_file()` with a permission-denied file returns `status="error"`, does not raise
- [ ] Integration: `run_scan()` on `testdata/plaintext/` produces correct CSV output
- [ ] Integration: `run_scan()` on `testdata/` with all output formats — CSV, JSON, text files all created and non-empty
- [ ] Integration: `CliRunner` invokes `piidigger scan` and exits 0

### Phase 3 — Exit Criteria

- [ ] Full scan of `testdata/` produces correct output in all three formats
- [ ] All handler unit tests pass without spinning up a process tree
- [ ] `CliRunner` smoke test passes
- [ ] `ruff` + `mypy` clean on all Phase 3 files

---

## Phase 4 — Hardening & Parity

*Reliability, the 2.0 output baseline, deletion of old code, and coverage floor.*

### Heartbeat deadline monitoring (completing the Phase 2 stub)

- [ ] `_check_worker_deadlines()` full implementation:
  - [ ] Track `{task_id: (worker_pid, start_time)}` for all in-flight tasks
  - [ ] On expiry (`2 × task.timeout_seconds`): log warning with task details; `terminate()` pid; spawn replacement; synthesize `status="timeout"` result; decrement `pending`
  - [ ] Crash-before-heartbeat detection: if a worker process is dead (`not proc.is_alive()`) and its task has no heartbeat record, re-queue the task (up to `MAX_RETRIES`); log each retry; after max retries, synthesize `status="error"`
- [ ] `MAX_RETRIES` constant defined in `orchestration/worker.py`

### Reliability validation

- [ ] `base64-xml-test.xml` with email handler and `timeout_seconds=30` completes in < 5 minutes total
- [ ] Log contains an explicit `"timeout"` record for the offending file — not a silent skip
- [ ] Run with 1 deliberately hung worker (synthetic): other workers continue; hung worker is terminated and replaced; scan completes

### Baseline comparison

- [ ] Generate 1.x baseline output: run the current `main` branch against `testdata/` and save CSV, JSON, text to `tests/fixtures/baseline_results/`
- [ ] Generate 2.0 output: run `run_scan()` against the same `testdata/`
- [ ] Compare CSV: same row count; same columns; rows match (order-independent sort)
- [ ] Compare JSON: same result count; same field values (order-independent)
- [ ] Compare text: same line count; same content
- [ ] Document any intentional format differences (lineage fields added as nulls in 2.0 are an expected delta)

### Old code deletion

- [ ] Delete `src/piidigger/classes.ProcessManager`
- [ ] Delete `src/piidigger/queuefuncs.py`
- [ ] Delete `src/piidigger/filescan.py`
- [ ] Remove from `piidigger.py`: `fileHandlerDispatcher`, `getOutputHandlers`, `progressLineWorker`, all SENTINEL references, all `mp.Value` totals, all old `ProcessManager` instances
- [ ] Delete `src/piidigger/globalvars.SENTINEL` (and any remaining dead vars)
- [ ] Confirm `piidigger.py` is either deleted or reduced to a thin shim (or fully replaced by `cli/` + `run.py`)
- [ ] `grep -r "SENTINEL\|dirsQ\|filesQ\|resultsQ\|ProcessManager\|activeFilesQ"` returns no hits in `src/`

### Coverage and quality gate

- [ ] Run `pytest --cov=src/piidigger tests/ --cov-report=term-missing`
- [ ] Overall coverage ≥ 80%
- [ ] `orchestration/` coverage ≥ 90%
- [ ] `ruff check src/ tests/` — zero violations
- [ ] `mypy src/` — zero errors

### Phase 4 — Tests

- [ ] Integration: timeout fires correctly — `base64-xml-test.xml` with 5s timeout produces a timeout result in < 15s
- [ ] Integration: worker crash recovery — synthetic crash; task is re-queued; scan completes
- [ ] Integration: all old orchestration code deleted — `grep` assertions pass in CI
- [ ] Integration: baseline comparison — output delta matches documented expectations only

### Phase 4 — Exit Criteria

- [ ] `base64-xml-test.xml` completes < 5 minutes; timeout logged; run never hangs
- [ ] Graceful `Ctrl+C` with full cleanup (no orphan processes; no temp files)
- [ ] Baseline comparison passes (or delta is documented)
- [ ] No old orchestration code in `src/`
- [ ] Coverage ≥ 80%; `ruff` + `mypy` clean

---

## Phase 5 — ZIP Support

*Proves Goal 2: ZIP adds task types and a `ScannableItem` producer with zero changes to `coordinator.py` or `worker.py`.*

**Pre-requisite:** resolve Open Decision 6 (secure deletion library) before writing any `materialize()` implementation that extracts archive members.

### Open Decision 6 — secure deletion library

- [ ] Research PyPI packages: evaluate maintenance status, cross-platform support (Windows/macOS/Linux), SSD vs. HDD behavior, license
- [ ] Select package; add to project dependencies
- [ ] Document the choice and its trade-offs in a comment at the call site

### Archive source — `src/piidigger/orchestration/sources.py`

- [ ] `ArchiveMemberItem(ScannableItem)`: wraps a `zipfile.ZipFile` + member name
  - [ ] `display_path`: `f"{archive_path}::{member_name}"`
  - [ ] `ext`: suffix of `member_name`
  - [ ] `mime`: sniffed from stream header (or `None`)
  - [ ] `size`: `ZipInfo.file_size` (uncompressed)
  - [ ] `depth`: parent archive depth + 1
  - [ ] `open_stream()`: `zipfile.open(member)` — stream, no extraction
  - [ ] `materialize()`: extract member to per-task temp dir using secure deletion on cleanup

### Task types and handlers

- [ ] Add to `TaskType` enum: `ENUM_ARCHIVE_MEMBERS`, `SCAN_ARCHIVE_MEMBER`
- [ ] `models/payloads.py`: `EnumArchiveMembersPayload`, `ScanArchiveMemberPayload` (per [ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md))
- [ ] `handle_enum_archive_members(task, ctx, logger) -> TaskResult`:
  - [ ] Validates payload; opens archive
  - [ ] Enumerates members with all safety checks (path traversal, encryption, size, member count, compression ratio)
  - [ ] Returns `new_tasks`: one `SCAN_ARCHIVE_MEMBER` per accepted member
  - [ ] Returns `counters`: `{"archives_scanned": 1, "archive_members_found": N, "archive_members_skipped": K}`
- [ ] `handle_scan_archive_member(task, ctx, logger) -> TaskResult`:
  - [ ] Constructs `ArchiveMemberItem`
  - [ ] Routes through `FileHandler` + `DataHandler` chain (same as `handle_scan_file`)
  - [ ] Returns findings with lineage fields populated
- [ ] Add both handlers to `DISPATCH` table — confirm `coordinator.py` and `worker.py` needed no other changes

### Configuration additions

- [ ] Add `ArchiveConfig` nested model to `Config`:
  - [ ] `enabled: bool = True`
  - [ ] `formats: list[str] = ["zip"]`
  - [ ] `max_depth: int = Field(default=1, ge=0, le=3)`
  - [ ] `max_members: int = 10_000`
  - [ ] `max_member_uncompressed_size_mb: int = 50`
  - [ ] `max_total_uncompressed_size_mb: int = 1024`
  - [ ] `task_timeout_seconds: int = 30`
- [ ] Add TOML section `[archives]` to default config template

### CLI additions

- [ ] `--archives-enabled / --no-archives`
- [ ] `--archive-max-depth INT`
- [ ] `--archive-max-members INT`
- [ ] `--archive-max-member-size-mb INT`

### Safety controls (all must be tested)

- [ ] Reject path-traversal member names (`../`, absolute paths)
- [ ] Reject encrypted members
- [ ] Reject members exceeding `max_member_uncompressed_size_mb`
- [ ] Stop enumeration when `max_members` exceeded
- [ ] Stop when running total uncompressed size exceeds `max_total_uncompressed_size_mb`
- [ ] Reject members with compression ratio > 1000× (bomb heuristic)
- [ ] Reject archives with invalid central directory

### Progress counters

- [ ] Add to `ProgressDisplay`: `archives_found`, `archives_scanned`, `archive_members_found`, `archive_members_scanned`, `archive_members_skipped`, `archive_errors`

### Test fixtures — `testdata/zip/`

- [ ] `simple-pii.zip` — known PII findings
- [ ] `nested-depth-2.zip` — nested archive
- [ ] `oversize-member.zip` — triggers size limit
- [ ] `many-members.zip` — triggers member count limit
- [ ] `traversal-member.zip` — triggers path-traversal rejection
- [ ] `encrypted-member.zip` — triggers encryption rejection
- [ ] `corrupt.zip` — triggers bad central directory handling
- [ ] `zip-bomb-simulated.zip` — triggers compression ratio rejection (safe synthetic)

### Phase 5 — Tests

All per [ZIP_HANDLING_PLAN.md § 12](./ZIP_HANDLING_PLAN.md).

- [ ] Unit: each safety rejection rule fires independently
- [ ] Unit: `ArchiveMemberItem.open_stream()` yields correct bytes
- [ ] Unit: `ArchiveMemberItem.materialize()` creates temp file; cleanup deletes it securely
- [ ] Integration: scan `simple-pii.zip` — findings include correct `display_path` and lineage fields
- [ ] Integration: scan `nested-depth-2.zip` with `max_depth=1` — nested members skipped and logged
- [ ] Integration: scan ZIP containing docx/xlsx members — binary members extracted and scanned correctly
- [ ] Resilience: `corrupt.zip` — handled without crash
- [ ] Resilience: timeout during member scan — task times out; scan continues
- [ ] Resilience: `Ctrl+C` during large archive — cleans up temp files
- [ ] Proof: confirm `coordinator.py` and `worker.py` have zero diff vs. end of Phase 4

### Phase 5 — Exit Criteria

- [ ] ZIP enumeration and member scanning run under the task queue architecture
- [ ] Zero changes to `coordinator.py` or `worker.py` vs. Phase 4 (verified by `git diff`)
- [ ] All safety limits active and verified by tests
- [ ] Findings include lineage fields; output format unchanged for non-archive results
- [ ] Secure deletion used for all `materialize()` temp files
- [ ] Test coverage ≥ 80% maintained
- [ ] `ruff` + `mypy` clean

---

## Overall Success Criteria

These must all be true before `refactor` is merged to `main`:

- [ ] Entire orchestration layer is new code under `orchestration/`; old process code deleted
- [ ] All identifiers snake_case / PascalCase / UPPER_CASE; ruff `N` + mypy clean
- [ ] Business logic unit-testable with no process tree
- [ ] `base64-xml-test.xml` completes < 5 minutes; timeout logged; run never hangs
- [ ] Graceful `Ctrl+C` with full cleanup (no temp files, no orphan processes)
- [ ] 2.0 output baseline set with lineage fields present; baseline comparison passes
- [ ] ZIP (Phase 5) added with zero changes to `coordinator.py` or `worker.py`
- [ ] Test coverage ≥ 80%

---

## Known Risks

| Risk | Mitigation |
|---|---|
| Windows `spawn` pickling surprises | Phase 1 integration test explicitly verifies `WorkerContext` across the spawn boundary before any business logic is attached |
| Coordinator termination never fires | Phase 2 tested on a synthetic tree before real I/O is wired; `pending` arithmetic unit-tested independently |
| Output format regression | Phase 4 baseline comparison run before any old code is deleted |
| ZIP temp extraction leaves PII on disk | `materialize()` only called when provably necessary; secure deletion library selected in Phase 5 pre-work; cleanup in `try/finally` |
| Crash-before-heartbeat orphans a task | Acknowledged gap; Phase 4 hardens with live-worker count check and task re-queue |
| `rich.Live` conflicts with test output | `ProgressDisplay` no-ops in non-TTY mode; tests run with `--capture=sys` or a non-TTY fixture |
