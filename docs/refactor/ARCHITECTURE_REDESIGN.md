# PIIDigger Architecture Redesign

**Branch**: `refactor`
**Status**: Implemented — retained as historical design reference. All 6 phases described here are complete; this document predates the `worker.py` → `worker/` package split and the `archivehandlers/` package, so treat [docs/architecture/orchestration/coordinator-worker-pipeline.md](../architecture/orchestration/coordinator-worker-pipeline.md) and [docs/architecture/archives/archive-handling.md](../architecture/archives/archive-handling.md) as authoritative for current code structure where they differ from what's described below.
**Last Updated**: 2026-07-06
**Target release**: 2.0.0

## Table of Contents
1. [Goals & Non-Goals](#goals--non-goals)
2. [Scope: What Is Discarded vs. Kept](#scope-what-is-discarded-vs-kept)
3. [Architecture Overview](#architecture-overview)
4. [The Task Model](#the-task-model)
5. [Lite Dependency Injection: WorkerContext](#lite-dependency-injection-workercontext)
6. [The Generic Worker](#the-generic-worker)
7. [The Coordinator: Fan-out, Termination, Progress](#the-coordinator-fan-out-termination-progress)
8. [Logging Architecture](#logging-architecture)
9. [Progress Reporting](#progress-reporting)
10. [Reliability: Timeouts & Worker Restart](#reliability-timeouts--worker-restart)
11. [New Business-Logic Protocols](#new-business-logic-protocols)
12. [The ScannableItem Abstraction (ZIP Seam)](#the-scannableitem-abstraction-zip-seam)
13. [Result & Output Schema (with Lineage)](#result--output-schema-with-lineage)
14. [Configuration Model](#configuration-model)
15. [Code Standards](#code-standards)
16. [Module Layout](#module-layout)
17. [Implementation Phases](#implementation-phases)
18. [Open Decisions](#open-decisions)

---

## Goals & Non-Goals

The 1.x architecture was an honest first attempt that has been outgrown. This refactor optimizes for four explicit goals, in priority order:

1. **Reliability** — no hangs, no deadlocks, no lost results. A pathological file (e.g. catastrophic regex backtracking) must never stall or freeze the run.
2. **Extensibility** — adding a feature should mean adding a task type and a handler, not re-choreographing process coordination. **ZIP archive support is the first such feature and the acceptance test for this goal** (see [ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md)).
3. **Maintainability** — one obvious way to wire dependencies, consistent naming, typed interfaces, no clever cross-process coordination tricks.
4. **Testability** — business logic and orchestration are both unit-testable without spinning up the whole process tree.

**Non-goals for 2.0:** changing what counts as PII, async/await rewrite, distributed/multi-host scanning, or supporting archive formats beyond ZIP (ZIP is the pattern; others follow later).

---

## Scope: What Is Discarded vs. Kept

This is a clean-slate rewrite of the orchestration layer. There is **no incremental migration of the process code** — it is replaced wholesale.

### Discarded entirely
- `ProcessManager` and all `*PM` instances.
- `find_dirs_worker` / `find_files_worker` / `file_handler_dispatcher` / `progress_line_worker`.
- The `SENTINEL` protocol, `queuefuncs`, the `active_files_q_processes` counter, and the named-queue dict (`dirsQ`, `filesQ`, `*_resultsQ`).
- The shared `mp.Value` `totals` dict and its lock-guarded increments.
- The subprocess-based progress line.

### Kept, but re-contracted
The three business-logic layers survive because the PII-matching and file-reading logic is correct. Every layer gets a new, narrower interface (details in [New Business-Logic Protocols](#new-business-logic-contracts)):
- **Data handlers** (`datahandlers/`): the PII matchers (pan, email, phonenum, trackdata).
- **File handlers** (`filehandlers/`): readers that turn a file into text chunks (plaintext, pdf, docx, xlsx, xls).
- **Output handlers** (`outputhandlers/`): the CSV/JSON/text writers, recast as **sinks** that no longer own a queue loop.

### Net effect
After the refactor the only multiprocessing-aware code lives in `orchestration/`. Business logic becomes pure, synchronous, and trivially unit-testable.

---

## Architecture Overview

The pattern is a **single coordinator feeding a pool of identical workers through one task queue, collecting results from one result queue, and fanning discovered work back into new tasks until the work set is empty.**

```
                  ┌─────────────────────────────────────────────┐
                  │            Coordinator (main process)        │
                  │  • seeds initial tasks (start dirs)          │
                  │  • drains result_queue                       │
                  │  • turns results into follow-up tasks        │
                  │  • owns progress display (rich.Live)         │
                  │  • owns and flushes output sinks             │
                  │  • detects run completion (no pending tasks) │
                  │  • monitors worker deadlines; restarts hung  │
                  └──────────┬──────────────▲──────────┬────────┘
             task_queue ─────┘              │          │ log records
                                     result_queue      │
                  ┌──────────────────────── │ ─────────┼────────┐
                  │   Worker pool — N identical workers          │
                  │   get(task) → dispatch → put(result)         │
                  │                                    │ log rec │
                  └────────────────────────────────────┼────────┘
                                                        │
                                             ┌──────────▼──────────┐
                                             │   Logging listener   │
                                             │ (drains log_queue →  │
                                             │  FileHandler)        │
                                             └─────────────────────┘
```

**All processes — coordinator and workers alike — send log records on `log_queue`.** Operational events like "starting N worker processes", "restarting hung worker", and "beginning scan of path X" originate in the coordinator. Worker events like "processing file Y" or "timeout on Z" originate in workers. One listener drains all of it.

Key invariants that deliver the goals:

- **Single task source.** Only the coordinator enqueues tasks. Workers never enqueue; they report *discovered work* in their results, and the coordinator decides what becomes a new task. This makes recursive fan-out (dirs→files→scans, and later archive→members) uniform, and ZIP is a drop-in.
- **Workers are stateless and identical.** No roles, no per-type process counts, automatic load balancing. Adding a task type never changes the pool.
- **Termination is a property of the work set, not a signal.** The run is done when there are zero outstanding tasks and the result queue is drained. There is no SENTINEL chain to break.
- **One way to get dependencies.** Everything a worker needs arrives in a single `WorkerContext` (see below).

---

## The Task Model

Pydantic v2, frozen, validated at the IPC boundary so a malformed task can never reach a handler. Payloads are typed per task type rather than a bare `dict`.

> Illustrative, not final — field names settle during Phase 1.

```python
from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskType(str, Enum):
    ENUM_DIR = "enum_dir"           # list one directory → child dirs + scannable files
    SCAN_FILE = "scan_file"         # read + match one scannable item
    # Archive types arrive with ZIP (Phase 5), no orchestration change required:
    ENUM_ARCHIVE_MEMBERS = "enum_archive_members"
    SCAN_ARCHIVE_MEMBER = "scan_archive_member"


class Task(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    task_type: TaskType
    payload: dict                       # validated per-type by the handler on entry
    timeout_seconds: int = Field(default=30, ge=1, le=600)


class TaskResult(BaseModel):
    task_id: str
    task_type: TaskType
    status: Literal["ok", "timeout", "error"]
    # Work this task discovered that the coordinator should enqueue as new tasks:
    new_tasks: list[dict] = Field(default_factory=list)
    # PII findings to be routed to output sinks (see Result & Output Schema):
    findings: list[dict] = Field(default_factory=list)
    # Per-counter increments for the progress display (e.g. {"files_scanned": 1}):
    counters: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None
    duration_seconds: float = Field(default=0.0, ge=0.0)
    worker_pid: int | None = None
```

`new_tasks` + `counters` are what let the coordinator drive fan-out and progress without any shared mutable state. A lightweight `TaskStarted` heartbeat message (not a `TaskResult`) is also put on the result queue when a worker picks up a task, to support deadline monitoring (see [Reliability](#reliability-timeouts--worker-restart)).

---

## Lite Dependency Injection: WorkerContext

Every 1.x process target took a long positional tuple (`config, queues, totals, stop_event, log_manager, ...`). That tuple was the extensibility problem — adding a process type meant touching every call site. The replacement is a single context object passed to every worker and handler.

**Pydantic is the project standard for all models. `WorkerContext` is the named exception:** it holds `mp.Queue`, `mp.Event`, and `mp.Value` — opaque OS-level objects with no meaningful schema that Pydantic could validate. For these, a frozen `dataclass` is correct; Pydantic's value is at data validation boundaries, and these aren't boundaries.

```python
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass

from piidigger.models.config import Config


@dataclass(frozen=True)
class WorkerContext:
    """All shared state a worker needs. Must contain only pickle-safe members
    because it crosses the spawn boundary on Windows/macOS.

    mp.Queue, mp.Event: picklable (they are proxy objects to shared OS resources).
    Config: picklable (plain validated Pydantic model with no open handles).
    logging.Logger: NOT allowed here — build it inside each process from log_queue.
    rich.Console: NOT allowed here — owns the terminal, must stay in the coordinator.
    """
    config: Config
    task_queue: mp.Queue
    result_queue: mp.Queue
    log_queue: mp.Queue
    stop_event: mp.Event
```

Handlers take the context, not a grab-bag of args:

```python
def handle_scan_file(task: Task, ctx: WorkerContext) -> TaskResult: ...
```

Testing benefit: a unit test builds a `WorkerContext` with `queue.SimpleQueue()` fakes and calls a handler directly. No process tree required.

---

## The Generic Worker

One function, one loop, one dispatch table. Adding a task type adds one entry to the table.

```python
DISPATCH: dict[TaskType, Callable[[Task, WorkerContext, Logger], TaskResult]] = {
    TaskType.ENUM_DIR:   handle_enum_dir,
    TaskType.SCAN_FILE:  handle_scan_file,
    # ENUM_ARCHIVE_MEMBERS / SCAN_ARCHIVE_MEMBER added in Phase 5 — no other change needed
}

def worker_loop(ctx: WorkerContext) -> None:
    logger = _build_logger(ctx.log_queue)           # built in-process, not passed in
    while not ctx.stop_event.is_set():
        task = ctx.task_queue.get()
        if task is SHUTDOWN:
            break
        ctx.result_queue.put(TaskStarted(task.task_id, os.getpid()))  # heartbeat
        try:
            result = _dispatch(task, ctx, logger)   # never raises; errors → TaskResult
        finally:
            _cleanup_temp_workspace()               # guaranteed, even on error
        ctx.result_queue.put(result)
```

`_dispatch` wraps handlers in `try/except` so a handler bug becomes a `status="error"` result, never a dead worker. Per-task temp workspace creation and guaranteed cleanup lives here (see [ScannableItem](#the-scannableitem-abstraction-zip-seam) for the security consideration around temp files).

---

## The Coordinator: Fan-out, Termination, Progress

The coordinator runs in the main process. Its loop drains results and re-seeds the task queue until no work remains.

**In plain terms:** the coordinator tracks a count of outstanding tasks. Every time it enqueues a task, the count goes up. Every time it processes a result, the count goes down — then immediately goes back up for any new tasks that result produced. When the count reaches zero, every task that was ever created has been accounted for, and the run is done.

```python
for start_dir in config.start_dirs:
    task_queue.put(Task(task_type=TaskType.ENUM_DIR, payload={"path": start_dir}))
pending = len(config.start_dirs)

while pending > 0:
    try:
        msg = result_queue.get(timeout=HEARTBEAT_CHECK_INTERVAL)
    except queue.Empty:
        _check_worker_deadlines(...)    # detect hung/crashed workers; synthesize result if needed
        continue

    if isinstance(msg, TaskStarted):
        _record_heartbeat(msg)          # does not change pending
        continue

    # msg is a TaskResult
    pending -= 1
    for new_task_payload in msg.new_tasks:
        task_queue.put(Task(**new_task_payload))
        pending += 1
    for finding in msg.findings:
        _route_to_sinks(finding)
    progress.update(msg.counters)

# pending == 0: all work accounted for
_broadcast_shutdown()
_join_workers()
_flush_sinks()
_stop_log_listener()
```

**Why `pending == 0` cannot be premature:** the coordinator is single-threaded. The sequence `pending -= 1 → enqueue new_tasks → pending += len(new_tasks)` happens in one iteration, never interrupted. `pending` is only tested at the `while` guard, which runs after all new tasks from the current result have already been counted. A genuine zero means every task ever enqueued has produced exactly one result — true termination.

**The crash-before-heartbeat gap:** if a worker crashes after dequeuing a task but before sending its heartbeat, the coordinator has no record the task was taken. This edge case is handled in Phase 4 (hardening) by tracking task dispatch times and counting live workers; tasks orphaned by a crash are re-queued. It is noted here so the Phase 1 worker design leaves room for it.

**Keyboard interrupt / graceful stop:** `Ctrl+C` raises `KeyboardInterrupt` in the coordinator (main process). Workers receive `SIGINT` automatically. The coordinator's `except KeyboardInterrupt` block calls `_broadcast_shutdown()`, joins workers with a timeout, flushes any partial findings to sinks, and exits. Workers catch `KeyboardInterrupt` in their own loop and exit cleanly after finishing their current task.

---

## Logging Architecture

The queue-based model is correct for multiprocessing and is kept, with lifecycle fixes:

- Every process — coordinator and workers — logs via `logging.handlers.QueueHandler(ctx.log_queue)`. The `Logger` is **built inside each process**; it is never passed across the spawn boundary.
- A single **`logging.handlers.QueueListener`** (running as a thread in the coordinator) drains `log_queue` to the `FileHandler`. This replaces the hand-rolled `log_processor` subprocess and its `sleep(2)` shutdown.
- The listener starts **before** any worker and stops **after** all workers have joined, so final log records are never lost.
- Log records (diagnostics) flow on `log_queue`. Findings, counters, and task status flow on `result_queue`. They are never mixed.

---

## Progress Reporting

Progress moves entirely into the coordinator, which already sees every result. No shared `mp.Value` counters, no locks, no separate progress process.

The coordinator maintains plain in-process integer counters, updated from `TaskResult.counters`. Single writer, no races.

**Display:** a two-panel `rich.Live` layout owned by the coordinator in the main process (the only correct place to control the terminal):

- **Top panel — counters/progress bars:** `rich.Progress` bars for dirs found/scanned, files found/scanned, bytes processed, and results found. Matches the current progress line layout but rendered graphically.
- **Bottom panel — events log:** a scrolling window (fixed height, circular buffer) showing warnings, errors, and notable events in real time — e.g. timeout on a file, a skipped archive member, a permission error. Populated from a secondary in-memory queue that the coordinator also writes to when routing error/warning results.

This uses `rich.Layout`, `rich.Live`, and `rich.Table` (for the scrolling events panel). Rich handles terminal width and graceful degradation when stdout is not a TTY (e.g. CI, piped output) — in non-TTY mode, progress output is suppressed and only final summary is printed.

---

## Reliability: Timeouts & Worker Restart

The headline reliability problem — a 1.5 MB base64 XML file hanging for minutes on catastrophic regex backtracking — is solved at the process level.

**Chosen strategy: heartbeat + targeted restart.**

1. Before executing a task, the worker puts a lightweight `TaskStarted(task_id, pid)` heartbeat on the result queue.
2. The coordinator records the dispatch time and owning pid for every in-flight task.
3. On each `result_queue.get(timeout=...)` expiry, the coordinator scans in-flight tasks for any whose deadline (`2 × timeout_seconds`) has passed.
4. For an expired task: the coordinator calls `worker_process.terminate()`, spawns a replacement worker, and synthesizes a `status="timeout"` `TaskResult` for the task (decrementing `pending` correctly).
5. The run continues; the offending file is reported as a timeout in logs and output, never silently dropped, never blocking the pool.

Steady-state cost is zero extra processes. The cost is paid only on an actual hang.

> **Alternative considered and rejected:** `mp.Pool(1)` per task. Reliable but spawns a process per file — unacceptable overhead on Windows `spawn`. Recorded here in case profiling changes the trade-off on a future platform.

---

## New Business-Logic Protocols

The retained logic gets narrow, synchronous, dependency-free interfaces. None of them import `multiprocessing`, queues, or loggers directly. All protocols live in `protocols.py`.

**Data handler** — a PII matcher. Stateless and pure:
```python
class DataHandler(Protocol):
    name: str
    def find_matches(self, text: str) -> dict[str, set[str]]: ...
```

**File handler** — reads a source item and yields text chunks. Takes an abstract `ScannableItem`, not a path string:
```python
class FileHandler(Protocol):
    def read(self, source: ScannableItem) -> Iterator[str]: ...
```

**Output sink** — receives findings from the coordinator and writes them. No queue loop:
```python
class OutputSink(Protocol):
    def open(self) -> None: ...
    def write(self, record: ResultRecord) -> None: ...
    def close(self) -> None: ...
```

Sinks are opened before the coordinator loop starts, receive `write()` calls per finding, and are closed after the loop ends. Trivially testable with a `tmp_path`.

---

## The ScannableItem Abstraction (ZIP Seam)

The 1.x `classes.File` assumes a real `pathlib.Path` with `stat()`, `parent`, and `suffix`. ZIP members have none of those. The abstraction is introduced in the core so that ZIP simply adds a new *producer* of it, not a new code path through every handler.

```python
class ScannableItem(Protocol):
    display_path: str        # see archive path notation below
    ext: str
    mime: str | None
    size: int
    depth: int               # 0 for on-disk files, ≥1 inside archives
    def open_stream(self) -> IO[bytes]: ...
    def materialize(self) -> Path: ...
```

**Concrete implementations:**
- `FilesystemItem` (Phase 3): wraps a real `pathlib.Path`. `open_stream()` opens it; `materialize()` returns the path itself (no copy).
- `ArchiveMemberItem` (Phase 5): wraps a `zipfile` member. `open_stream()` reads the compressed member directly. `materialize()` extracts to a temp file.

**File handlers consume `ScannableItem`**, so they are agnostic to where the bytes come from. Handlers that can operate on a stream call `open_stream()`. Handlers that require a real path (currently xlsx/xls via openpyxl/xlrd) call `materialize()`.

**Security note on `materialize()`:** extracting an archive member to a temp file creates a second copy of potentially sensitive data (PII, card numbers) on disk. This is a known trade-off, mitigated as follows:
- `materialize()` is called only by handlers that provably cannot accept a stream — it is not a convenience fallback.
- The temp file lives in a per-task workspace directory, created and cleaned up inside the worker's `try/finally` block.
- Cleanup uses **secure deletion** (overwrite with random or zero bytes, then delete) via a stdlib or well-maintained PyPI package. Standard `os.unlink` leaves data recoverable; we must not use it for temp files that may contain PII. The specific package is selected in Open Decision 6 before Phase 5 implementation.
- Future refactors of xlsx/xls handlers to accept streams would eliminate this trade-off entirely.

**Archive member path notation:** there is no universally standardized format for displaying a path inside an archive. We adopt `::` as the separator (e.g. `archive.zip::path/inside/member.txt`) because it is unambiguous, avoids conflicts with Windows drive letter syntax (`C:\`), forward slashes, and backslashes, and is the convention used by several security scanning tools. This notation appears in `display_path`, logs, and output `source_member_path` fields.

---

## Result & Output Schema (with Lineage)

The 2.0 output baseline is set once and not changed. Because ZIP needs lineage fields in output, those fields are present from day one — as empty/null for on-disk files — so adding ZIP does not break baseline comparison.

```python
class ResultRecord(BaseModel):
    source_path: str                        # host file path (or the archive path for members)
    source_member_path: str | None = None   # member path within archive; None for on-disk
    source_depth: int = 0                   # 0 for on-disk, ≥1 for archive members
    source_container_type: str | None = None  # "zip" for members; None for on-disk
    handler: str                            # data handler name (e.g. "pan", "email")
    matches: dict[str, list[str]]           # match type → list of matched values
```

On-disk findings leave the member/container fields null. ZIP findings populate them. Same schema, same CSV columns, same JSON keys across the entire 2.0 lifetime.

---

## Configuration Model

The 1.x `Config` getter-soup (`getMaxProcs()`, `getDataHandlers()`, ...) is replaced by a validated Pydantic model with nested sections. Reasons:
- ZIP adds ~6 new settings; the getter pattern does not scale.
- Tasks already depend on Pydantic; no new dependency.
- Config errors (typos, wrong types, missing required fields) should fail at load with a clear message, not crash at runtime deep in a worker.
- Two existing bugs are closed by this rewrite: `classes.py:66` and `:110` reference `globalfuncs.errorCodes` (wrong module; `errorCodes` lives in `globalvars`), and `piidigger.py:288` references the non-existent key `errorCodes['unknown']` (correct key is `'unknownError'`). Both crash on the exact error path they were meant to handle.

---

## Code Standards

### snake_case — mandatory everywhere

Every identifier in the codebase moves to PEP 8. This applies to retained business logic, not just new code:

| Category | Convention | Examples |
|---|---|---|
| Functions, methods, variables, modules | `snake_case` | `find_files_worker`, `files_queue`, `handle_scan_file` |
| Classes | `PascalCase` | `WorkerContext`, `ScannableItem`, `Config` |
| Constants | `UPPER_CASE` | `SHUTDOWN`, `DEFAULT_TIMEOUT_SECONDS` |

Enforced by adding ruff's **`N` (pep8-naming)** ruleset to the existing `E,W,F,I,UP,RUF` selection, plus mypy `--strict`. The rename of retained handlers is a mechanical, zero-behavior-change commit that lands in **Phase 0**, so all new orchestration code is born snake_case from the start.

### Pydantic everywhere

All data models — tasks, results, payloads, config, `ResultRecord` — use Pydantic v2. Use plain `dataclass` only when holding types that Pydantic cannot meaningfully validate (currently: `WorkerContext` with its `mp.Queue`/`mp.Event` members). Document the reason at the class definition.

---

## Module Layout

```
src/piidigger/
│
├── cli/                          # Click CLI layer only; no business logic
│   ├── __init__.py
│   ├── main.py                   # Click group entry point; click.group()
│   └── commands/
│       ├── __init__.py
│       ├── scan.py               # `piidigger scan` (default command)
│       └── config.py             # `piidigger config` (generate, validate)
│
├── models/                       # All Pydantic data models
│   ├── __init__.py
│   ├── config.py                 # Config (replaces getter-soup in classes.py)
│   ├── tasks.py                  # Task, TaskResult, TaskType
│   ├── payloads.py               # Typed per-task-type payload models
│   └── results.py                # ResultRecord (with lineage fields)
│
├── protocols.py                  # Protocols: DataHandler, FileHandler,
│                                 #   OutputSink, ScannableItem
│
├── orchestration/                # All multiprocessing-aware code
│   ├── __init__.py
│   ├── context.py                # WorkerContext (dataclass — see note)
│   ├── worker.py                 # worker_loop + DISPATCH table
│   ├── coordinator.py            # fan-out loop, termination, deadline monitor
│   ├── logging_setup.py          # QueueHandler / QueueListener helpers
│   ├── progress.py               # rich.Live two-panel display
│   └── sources.py                # FilesystemItem; ArchiveMemberItem in Phase 5
│
├── datahandlers/                 # PII matchers (implement DataHandler)
│   ├── __init__.py
│   ├── pan.py
│   ├── email.py
│   ├── phonenum.py
│   └── trackdata.py
│
├── filehandlers/                 # File readers (implement FileHandler)
│   ├── __init__.py
│   ├── plaintext.py
│   ├── pdf.py
│   ├── docx.py
│   ├── xlsx.py
│   └── xls.py
│
├── outputhandlers/               # Output sinks (implement OutputSink)
│   ├── __init__.py
│   ├── csv.py
│   ├── json.py
│   └── text.py
│
└── run.py                        # run_scan(config: Config) -> int
                                  # The testable core; cli/commands/scan.py calls this
```

**Why `datahandlers/` / `filehandlers/` / `outputhandlers/` stay as flat directories:** each name is self-describing and maps directly to the contracts in `protocols.py`. A `services/` wrapper would add a directory level without adding clarity. The grouping is already implicit in the naming.

**Why `cli/` is a package:** a future `piidigger config generate` or `piidigger config validate` command is a natural addition. Starting with a Click group in `cli/main.py` costs nothing now and avoids a structural refactor later.

---

## Implementation Phases

Phases are natural, independently-testable breaks. Each leaves the tree in a known-good state and is a candidate standalone PR.

### Phase 0 — Standards & Scaffolding
*No behavior changes. Sets the baseline for all new code.*

- Global snake_case rename of all retained code (data handlers, file handlers, output handlers, remaining `classes.py` helpers).
- Enable ruff `N` ruleset; fix all violations; CI green.
- Extract `run_scan(config)` out of the Click `main()` into `run.py` so it is testable.
- Fix both `errorCodes` bugs (wrong module reference + wrong key) as part of the config cleanup.
- Scaffold empty module stubs for `orchestration/`, `models/`, `cli/`, `protocols.py`.

**Exit criteria:** ruff + mypy clean; existing test suite still passes; `run_scan` is callable in a test without Click.

---

### Phase 1 — Core Infrastructure
*Orchestration machinery with a trivial no-op task. No business logic attached yet.*

- `models/tasks.py`: `Task`, `TaskResult`, `TaskType`, heartbeat message.
- `orchestration/context.py`: `WorkerContext`.
- `orchestration/logging_setup.py`: `build_worker_logger()`, `start_listener()`, `stop_listener()`.
- `orchestration/worker.py`: `worker_loop` with dispatch table; handles `SHUTDOWN` and a `NOOP` task type for testing.
- Spin up a pool, dispatch 10 `NOOP` tasks, collect results, shut down cleanly — verified on Windows spawn.

**Exit criteria:** pool start/dispatch/result/shutdown proven on Windows; in-worker logging reaches the file; unit tests for `Task` validation.

---

### Phase 2 — Coordinator & Control Flow
*The riskiest piece: termination logic and graceful shutdown. Still no real scan logic.*

- `orchestration/coordinator.py`: fan-out loop; `pending` counter; heartbeat tracking; `_check_worker_deadlines()`; keyboard interrupt handling.
- `orchestration/progress.py`: two-panel `rich.Live` display wired to coordinator counters.
- Simulated task chain: `ENUM_DIR` stub returns synthetic `new_tasks` of `SCAN_FILE`; `SCAN_FILE` stub returns synthetic counters. End-to-end fan-out verified without real file I/O.
- Graceful `Ctrl+C` tested: partial results flushed, workers joined, no orphan processes.

**Exit criteria:** coordinator reaches `pending == 0` correctly on simulated tree; Ctrl+C exits cleanly within 5 seconds.

---

### Phase 3 — Re-contract & Wire Business Logic
*First real scan. Business logic re-contracted and re-attached under the new interfaces.*

- `protocols.py`: `DataHandler`, `FileHandler`, `OutputSink`, `ScannableItem` protocols.
- `orchestration/sources.py`: `FilesystemItem`.
- `models/config.py`: validated `Config` (replaces `classes.py` getter-soup).
- `models/results.py`: `ResultRecord` with lineage fields.
- Real `handle_enum_dir` (walks a directory, produces `ENUM_DIR` + `SCAN_FILE` tasks).
- Real `handle_scan_file` (uses `FileHandler` + `DataHandler` chain, returns findings).
- All `outputhandlers/` re-implemented as `OutputSink`s.
- Re-contracted `datahandlers/` and `filehandlers/` (snake_case, `ScannableItem`-based).
- `cli/` scaffolding wired to `run_scan()`.
- End-to-end directory scan produces correct CSV/JSON/text output.

**Exit criteria:** full scan of `testdata/` produces correct output; unit tests for each handler in isolation (no process tree).

---

### Phase 4 — Hardening & Parity
*Reliability, the output baseline, and coverage floor.*

- Heartbeat deadline monitoring and targeted worker restart.
- `base64-xml-test.xml` completes in < 5 minutes with timeout logged.
- Crash-before-heartbeat re-queue logic.
- Baseline comparison: 2.0 output vs. 1.x baseline (CSV, JSON, text).
- Old `ProcessManager`, `queuefuncs`, `filescan`, `classes.ProcessManager`, `SENTINEL` deleted.
- Test coverage ≥ 80%; ruff + mypy fully clean.

**Exit criteria:** all reliability tests pass; baseline comparison clean; no old orchestration code remains.

---

### Phase 5 — ZIP Support (First Feature)
*Proves Goal 2: ZIP adds a task type and a ScannableItem producer with zero changes to worker or coordinator.*

- `orchestration/sources.py`: `ArchiveMemberItem`.
- `TaskType.ENUM_ARCHIVE_MEMBERS` + `TaskType.SCAN_ARCHIVE_MEMBER` added to dispatch table.
- `handle_enum_archive_members` and `handle_scan_archive_member` handlers.
- Archive config section, CLI flags, safety limits.
- All requirements from [ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md).

**Exit criteria:** ZIP enumeration and member scanning run under the task queue architecture with no changes to `coordinator.py` or `worker.py`.

---

## Open Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Coordinator in main process vs. its own process | **Decided: main process.** Simplicity is its own benefit; revisit only if result-drain loop provably becomes a bottleneck. |
| 2 | Timeout mechanism | **Decided: heartbeat + targeted worker restart.** Per-task `mp.Pool(1)` rejected for Windows spawn overhead. |
| 3 | Config/model library | **Decided: Pydantic v2 throughout.** Only `WorkerContext` uses `dataclass` (documented exception). |
| 4 | Temp workspace scope | **Decided: per-task, always cleaned in `try/finally`.** Streaming via `open_stream()` is the preferred path; `materialize()` to a temp file is a named fallback only for handlers that provably cannot accept a stream. The security trade-off (temp PII copy) is documented and the footprint is minimized. |
| 5 | Log listener implementation | **Decided: `QueueListener` thread in coordinator.** Simpler than a dedicated process; revisit if file I/O contends with the result-drain loop. |
| 6 | Secure deletion library for temp files | **Decided: no external dependency.** Implemented as a hand-rolled 2-pass overwrite (zeros then random) + `os.fsync()` + `unlink()` in `orchestration/secure_delete.py` — cross-platform stdlib-only, no PyPI package needed. |

---

## Success Criteria — all met (verified 2026-07-06)

- [x] Entire orchestration layer is new code under `orchestration/`; old process code deleted. — `classes.py`, `piidigger.py`, `queuefuncs.py`, `filescan.py`, `globalvars.py` no longer exist; enforced in CI by `test_phase4.py::test_no_legacy_orchestration_references`.
- [x] All identifiers snake_case / PascalCase / UPPER_CASE; ruff `N` + mypy clean. — `ruff check src/ tests/` and `mypy src/` both clean.
- [x] Business logic unit-testable with no process tree. — handler unit tests call handler functions directly.
- [x] `base64-xml-test.xml` completes < 5 minutes; timeout logged; run never hangs. — resolved at the root cause (regex catastrophic backtracking) via an `@` prefilter; see `test_email.py::test_email_prefilter_skips_regex_on_long_no_at_string`.
- [x] Graceful `Ctrl+C` with full cleanup (no temp files, no orphan processes). — verified on POSIX (`test_coordinator.py::test_ctrl_c_exits_within_5_seconds`); Windows lacks an automated cross-process SIGINT test harness (`mp.Process` limitation), not a gap in `run_coordinator()`'s `KeyboardInterrupt` handling itself.
- [x] 2.0 output baseline set with lineage fields present; baseline comparison passes. — one-time migration validation: v2 is a strict superset of v1 (0 regressions, 4 improvements).
- [x] Archive support (Phase 5 — zip, then generalized to 7z and tar) adds task types and a `ScannableItem` producer with zero changes to `coordinator.py` or `worker.py`.
- [x] Test coverage ≥ 80%. — 84% overall.
