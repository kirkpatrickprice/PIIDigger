# Current Implementation Issues & Technical Debt

**Branch**: `refactor`
**Status**: Historical Reference — Architecture Decided
**Last Updated**: 2026-06-15

This document catalogs the specific problems in the 1.x codebase that motivated the 2.0 refactor. It is now a **historical record**: the architecture decisions that address each issue are captured in [ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md) and the work is tracked in [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md). Issues already resolved by pre-refactor commits are marked **[DONE]**.

---

## Already Resolved (Pre-Refactor Commits)

The following issues were addressed on the `refactor` branch before the orchestration rewrite began, and are closed:

| Issue | Resolution |
|---|---|
| argparse CLI — verbose, hard to extend | **[DONE]** Migrated to Click |
| Hand-rolled colorama/ctypes terminal code | **[DONE]** Replaced with Rich (`console.py`) |
| `tomli` third-party TOML dependency | **[DONE]** Replaced with `stdlib tomllib` |
| camelCase naming in CLI layer | **[DONE]** Click migration used snake_case |
| No linting or type-checking in CI | **[DONE]** ruff + mypy gates added |

---

## 1. Process Architecture Issues

### 1.1 SENTINEL-Based Deadlock Risk

**File**: `piidigger.py` — `file_handler_dispatcher`

If a worker hangs (e.g. email regex on `base64-xml-test.xml`), it never reaches the SENTINEL decrement. The next worker waiting on `filesQ.get()` never receives its signal. The entire pipeline deadlocks: one worker consuming CPU, N-1 workers waiting, queue empty.

**2.0 Resolution**: Heartbeat + targeted worker restart. A hung worker is detected by the coordinator and forcibly terminated. No chain to break. See [ARCHITECTURE_REDESIGN.md § Reliability](./ARCHITECTURE_REDESIGN.md#reliability-timeouts--worker-restart).

---

### 1.2 Race Condition in `active_files_q_processes` Counter

**File**: `piidigger.py` lines ~120-121, ~215-218

The counter serves dual purpose (tracking + termination signal). If a worker crashes without decrementing, the counter goes out of sync. Last-worker detection fails; pipeline deadlocks.

**2.0 Resolution**: No manual counter. The coordinator's `pending` variable tracks outstanding tasks with no locks — single writer, single reader, no races.

---

### 1.3 Inability to Add New Process Types

Adding a new process type required: new queue(s), new `ProcessManager` registration, modified SENTINEL coordination, updated counter management, updated shutdown choreography — touching every layer and risking the existing handoff chain. Parallel file enumeration was attempted and abandoned for this reason.

**2.0 Resolution**: Adding a task type adds one entry to the `DISPATCH` table and one handler function. No other code changes.

---

### 1.4 Fixed Process Role Specialization / Load Imbalance

`findDirsWorker`, `findFilesWorker`, and `fileHandler` had fixed roles. Slow directory enumeration left file handlers idle; slow file scanning left everything else idle. No dynamic rebalancing.

**2.0 Resolution**: All workers are identical. They pull from a single task queue. Load balancing is automatic.

---

## 2. Timeout Mechanism Failures

### 2.1 Threading-Based Timeout Blocked by GIL

Threading was tried for timeout enforcement. Python cannot forcibly terminate a thread; `join(timeout=X)` returns but the worker thread still holds the GIL. Unusable for CPU-bound regex operations.

**2.0 Resolution**: Process-level termination. Each worker is a separate process with its own GIL. The coordinator calls `proc.terminate()` on expired workers.

---

### 2.2 Multiprocessing Timeout with File-Based IPC

A `multiprocessing.Process` + JSON result file approach was tried. Process termination mid-write produced corrupt JSON. Timeouts were configured but not reliably firing. `base64-xml-test.xml` still caused 2-4 minute hangs.

**2.0 Resolution**: Queue-based result passing. Coordinator-side deadline monitoring via heartbeats. No file-based IPC for results.

---

### 2.3 No Timeout Logging or Visibility

Timeouts produced no log messages. Users saw silent hangs with no indication of which file or handler was involved.

**2.0 Resolution**: Every timeout produces an explicit log entry (task_id, file path, handler, duration) and a `status="timeout"` result record visible in output.

---

## 3. Code Quality Issues

### 3.1 Naming Convention Violations

`dirsQ`, `filesQ`, `resultsQ`, `findDirsWorker`, `findFilesWorker`, `fileHandlerDispatcher` — mixed camelCase and abbreviated names throughout.

**2.0 Resolution**: Global snake_case rename in Phase 0. Enforced by ruff `N` ruleset in CI. No exceptions.

---

### 3.2 Missing Type Hints

Process functions had no type hints, making IDE support and static analysis impossible.

**2.0 Resolution**: 100% type hint coverage required. mypy `--strict` enforced in CI.

---

### 3.3 Inadequate Docstrings

Process functions and handlers had no docstrings.

**2.0 Resolution**: All public functions require docstrings. Protocol interfaces document their contracts.

---

### 3.4 Insufficient Test Coverage

No tests for process orchestration, queue coordination, or integration pipelines. Only data-handler and file-handler unit tests existed.

**2.0 Resolution**: Comprehensive test pyramid per [TESTING_STRATEGY.md](./TESTING_STRATEGY.md). Coverage ≥ 80% enforced in CI.

---

### 3.5 No Validated Data Models at IPC Boundary

Tasks and results were bare dicts or positional args. No validation on deserialization from queues.

**2.0 Resolution**: `Task` and `TaskResult` are Pydantic v2 models (frozen, validated). A malformed task cannot reach a handler.

---

## 4. Configuration Issues

### 4.1 Getter-Soup Config Class

`Config` in `classes.py` exposed ~12 single-purpose getter methods (`getMaxProcs()`, `getDataHandlers()`, ...). No nested structure, no IDE autocomplete, no validation, hard to extend.

**2.0 Resolution**: `Config` replaced by a validated Pydantic model with nested sections (`config.results`, `config.archives`, etc.) in `models/config.py`.

---

### 4.2 `errorCodes` Wrong Module Reference (Latent Bug)

`classes.py:66` and `:110` call `globalfuncs.errorCodes['invalidConfig']`, but `errorCodes` lives in `globalvars`, not `globalfuncs`. The invalid-config and missing-start-dir error paths crash with `AttributeError` instead of a clean exit. A sibling bug at `piidigger.py:288` references the non-existent key `errorCodes['unknown']` (correct key: `'unknownError'`).

**2.0 Resolution**: Both bugs closed as part of the `Config` model rewrite in Phase 0/3.

---

### 4.3 Hard-to-Find Default Values

Defaults were embedded in a Python function, invisible to users and hard to compare against a custom config.

**2.0 Resolution**: Default config expressed as a TOML template (generated by `piidigger config generate`). Schema validation against the Pydantic model provides clear error messages.

---

## 5. Logging Issues

### 5.1 Hand-Rolled Queue Logging Lifecycle

`LogManager` used a custom `log_processor` subprocess with a `sleep(2)` shutdown and manual SENTINEL-on-logQ signaling. Hard to reason about; a blocked logQ could stall the pipeline; the 2-second sleep was a guess.

**2.0 Resolution**: Replaced by stdlib `logging.handlers.QueueListener` (a thread, not a process) started before any worker and stopped after all workers have joined. No sleeps, no manual SENTINEL on the log queue.

---

## 6. Edge Cases Not Handled

### 6.1 `base64-xml-test.xml` Catastrophic Backtracking

The email regex catastrophically backtracked on 1.5 MB of embedded base64 data, causing 2-4 minute hangs with no timeout enforcement.

**2.0 Resolution**: Per-task timeout (default 30s) enforced by coordinator deadline monitoring. Timeout is logged; run continues. Exit criterion: file completes in < 5 minutes.

---

### 6.2 Worker Process Crash

Worker crash → task lost → `pending` never decrements → coordinator never terminates. No recovery mechanism.

**2.0 Resolution**: Crash-before-heartbeat detection in Phase 4. Coordinator counts live workers; orphaned tasks are re-queued up to `MAX_RETRIES`, then synthesized as errors.

---

### 6.3 Shutdown During Active Scan

`Ctrl+C` during a scan left the SENTINEL choreography in an indeterminate state. Processes could remain alive; queues were not reliably drained; temp files were not cleaned up.

**2.0 Resolution**: `KeyboardInterrupt` caught in the coordinator. `broadcast_shutdown()` → join with timeout → flush sinks → stop log listener. Exit criterion: clean in < 5 seconds.

---

## Summary

| Issue | Category | 2.0 Disposition |
|---|---|---|
| SENTINEL deadlock | Architecture | Eliminated — heartbeat + restart |
| Race on counter | Architecture | Eliminated — no shared counter |
| Can't add process types | Architecture | Eliminated — DISPATCH table |
| Load imbalance | Architecture | Eliminated — shared task queue |
| GIL blocks threading timeout | Timeout | Eliminated — process-level termination |
| File-IPC timeout unreliable | Timeout | Eliminated — queue-based results |
| Silent timeouts | Timeout | Fixed — explicit log + result record |
| camelCase / abbreviations | Code quality | Fixed in Phase 0 — ruff N enforced |
| No type hints | Code quality | Fixed — mypy strict enforced |
| No docstrings | Code quality | Fixed — required by protocol |
| Insufficient tests | Code quality | Fixed — ≥ 80% coverage enforced |
| No IPC validation | Code quality | Fixed — Pydantic v2 on Task/TaskResult |
| Config getter-soup | Configuration | Fixed — Pydantic Config model |
| `errorCodes` wrong module | Configuration | Fixed in Phase 0 |
| Hard-coded defaults | Configuration | Fixed — TOML template + validation |
| Hand-rolled log lifecycle | Logging | Fixed — QueueListener |
| base64 hang | Edge case | Fixed — deadline + restart |
| Worker crash recovery | Edge case | Fixed in Phase 4 |
| Ctrl+C cleanup | Edge case | Fixed — coordinator shutdown sequence |
