# Current Implementation Issues & Technical Debt

**Branch**: `refactor`  
**Status**: Reference Document  
**Last Updated**: 2026-03-06

This document catalogs specific issues in the current codebase that the refactor will address.

---

## 1. Process Architecture Issues

### 1.1 SENTINEL-Based Deadlock Risk

**File**: `src/piidigger/piidigger.py` lines ~210-229 (fileHandler process)

**Issue**:
```python
# Current pattern
while True:
    item = getItem(queues['filesQ'])
    if item is SENTINEL:
        with activeFilesQProcesses.get_lock():
            activeFilesQProcesses.value -= 1
            if activeFilesQProcesses.value == 0:
                clearQ(queues['filesQ'])
            else:
                queues['filesQ'].put(SENTINEL)  # Signal next worker
        break
    # Process item...
```

**Problem**:
- If a worker hangs (e.g., email regex on base64-xml-test.xml), it never reaches the decrement
- Next worker waiting on `filesQ.get()` never receives the SENTINEL signal
- System deadlocks: one worker busy, N-1 workers waiting, queue empty

**Impact**: Occasional hangs even on successful scans. System appears to "freeze" while worker consumes CPU.

**Refactor Solution**: Task queue model - hung worker process can be terminated independently. No coordination chain to break.

---

### 1.2 Race Condition in activeFilesQProcesses Counter

**File**: `src/piidigger/piidigger.py` lines ~120-121, ~215-218

**Issue**:
```python
# Increment
with activeFilesQProcesses.get_lock():
    activeFilesQProcesses.value += 1

# ... (worker processing)

# Decrement
with activeFilesQProcesses.get_lock():
    activeFilesQProcesses.value -= 1
    if activeFilesQProcesses.value == 0:
        clearQ(queues['filesQ'])
```

**Problem**:
- If worker crashes/exits without decrementing, counter gets out of sync
- Lock contention on high-CPU workloads can cause delayed updates
- Counter serves dual purpose (tracking + termination signal) - fragile

**Impact**: Incorrect termination detection, potential deadlock if last-worker detection fails.

**Refactor Solution**: No manual counter management. Worker pool handles its own lifecycle. Results collected independently.

---

### 1.3 Inability to Add New Process Types

**Context**: User previously attempted to add parallel `fileScanner` process to rebalance work → **FAILED**

**Issue**:
- Current architecture: role-specialized processes with fixed queue connections
- Adding new process type requires:
  1. Creating new queue(s)
  2. Registering process in ProcessManager
  3. Modifying SENTINEL coordination logic
  4. Updating counter management
  5. Updating shutdown choreography
  6. Testing entire pipeline (risk of breaking existing handoff)

**Example**: To parallelize file enumeration, you'd need to:
- Create new queue between findDirsWorker and findFilesWorker
- Make findFilesWorker pull from multiple queues
- Update SENTINEL logic to account for multiple sources
- Risk: break dirsQ → filesQ coordination

**Impact**: Architecture is closed to extension. User gave up on parallelization.

**Refactor Solution**: Single task queue. Adding "parallel_enum_files" task type = add elif branch. Workers agnostic to task type.

---

### 1.4 Fixed Process Role Specialization

**File**: `src/piidigger/piidigger.py` lines ~408-428 (ProcessManager registration)

**Issue**:
```python
mainPM.register(target=filescan.findDirsWorker, name='findDirsWorker', num_processes=1, ...)
mainPM.register(target=filescan.findFilesWorker, name='findFilesWorker', num_processes=1, ...)
mainPM.register(target=fileHandlerDispatcher, name='fileHandler', num_processes=config.getMaxProcs(), ...)
```

**Problem**:
- Each process has one job
- If directory enumeration is slow: findFilesWorker idle (waiting for dirsQ), fileHandlers idle (waiting for filesQ)
- If file scanning is slow: dirsQ fills, findDirsWorker blocked, findFilesWorker blocked
- No dynamic rebalancing or work stealing

**Load Pattern Graph**:
```
Scenario: Slow directory enumeration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

findDirsWorker:  [BUSY] [BUSY]              [BUSY]
                 =============================== (enumerating 100k dirs)

findFilesWorker:                 [IDLE]... [IDLE]
                                 (waiting for dirsQ)

fileHandlers(8): [IDLE] [IDLE].. 
                 (waiting for filesQ)
```

**Impact**: Underutilization of CPU resources on typical mixed workloads.

**Refactor Solution**: All workers pull from single task queue. Naturally load-balanced.

---

## 2. Timeout Mechanism Failures

### 2.1 Threading-Based Timeout Blocked by GIL

**Attempted Solution** (early in session): Threading for timeout enforcement.

**Issue**:
```python
def call_with_timeout_thread(handler_func, timeout_sec):
    result = [None]
    def worker():
        result[0] = handler_func()
    
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=timeout_sec)  # Wait up to N seconds
    
    if thread.is_alive():
        # Thread still running - timeout occurred
        # BUT: we can't terminate a thread in Python!
```

**Problem**:
- Python Global Interpreter Lock (GIL) means main thread doesn't get CPU time while worker thread is busy
- `join(timeout=X)` expires, but worker thread still holds GIL
- Can't forcefully terminate thread (Python limitation)
- Main thread starved of GIL time, unable to proceed

**Impact**: **Threading unusable for CPU-bound operations** (regex pattern matching is CPU-bound).

**Refactor Solution**: Process-based timeout. Each process has separate GIL. Can forcefully terminate subprocess.

---

### 2.2 Multiprocessing Timeout with File-Based IPC

**Attempted Solution** (mid session): Multiprocessing with JSON file result passing.

**Issue**:
```python
def call_data_handler_with_timeout(handler_func, timeout_sec):
    result_file = f"/tmp/result_{uuid4()}.json"
    
    # Start handler in separate process
    proc = mp.Process(target=handler_func, args=(..., result_file))
    proc.start()
    proc.join(timeout=timeout_sec)
    
    if proc.is_alive():
        proc.terminate()  # Can forcefully terminate subprocess
        # But reading result_file may fail or be partial
```

**Problem**:
- File-based IPC unreliable (partial writes, not yet flushed)
- Process termination mid-write → corrupt JSON
- Reading result from incomplete file → parse errors
- Exception handling across process boundary difficult

**Impact**: Timeouts configured but not triggering. Logs show `Timeout=False` even with hangs. base64-xml-test.xml still causes 2-4 minute hang.

**Refactor Solution**: Queue-based result passing with `multiprocessing.Pool` API. Timeout handled at pool level, not file level.

---

### 2.3 No Timeout Logging or Visibility

**File**: `src/piidigger/globalfuncs.py` (timeout wrapper, if added in earlier edits)

**Issue**:
- Timeout mechanism added but produces no log messages
- User doesn't know which handlers timed out, which files, or how many times
- Silent failures difficult to debug

**Impact**: Users unaware of partial results or skipped processing.

**Refactor Solution**: Every task timeout → explicit log message with task_id, handler, file, timeout_duration.

---

## 3. Code Quality Issues

### 3.1 Naming Convention Violations

**File**: Scattered throughout codebase

**Issues**:
- Queue names: `dirsQ`, `filesQ`, `resultsQ` (abbreviated, not PEP8)
  - ✗ `getItem(queues['filesQ'])`
  - ✓ `get_task(queues['files_queue'])`
  
- Function names: mixed camelCase/snake_case
  - ✗ `findDirsWorker`, `findFilesWorker`, `fileHandlerDispatcher`
  - ✓ `find_dirs_worker`, `find_files_worker`, `file_handler_dispatcher`
  
- Variable names: abbreviated or unclear
  - ✗ `pm`, `dh`, `ft`, `cfg`
  - ✓ `process_manager`, `data_handler`, `file_type`, `config`

**Impact**: 
- Inconsistent codebase harder to maintain
- Violates PEP8, fails linter checks
- New contributors confused by style

**Refactor Solution**: Systematic rename to PEP8 conventions. Use linter-enforced formatting.

---

### 3.2 Missing Type Hints

**File**: All process and handler functions

**Issue**:
```python
# Current
def fileHandlerDispatcher(config, queues, totals, stopEvent, activeFilesQProcesses, logManager):
    # ... no type hints, unclear what each param is

# Should be
def file_handler_dispatcher(
    config: Config,
    queues: dict[str, mp.Queue],
    totals: dict[str, mp.Value],
    stop_event: mp.Event,
    active_files_queue_processes: mp.Value,
    log_manager: LogManager
) -> None:
    # ... clear intent, IDE support
```

**Impact**:
- No IDE autocomplete or inline documentation
- Static type checkers can't find bugs
- Self-documenting code (hints serve as documentation)

**Refactor Solution**: 100% type hint coverage across all files.

---

### 3.3 Inadequate Docstrings

**File**: Process functions and handlers

**Issue**:
```python
# Current: no docstring
def fileHandlerDispatcher(config, queues, ...):
    while True:
        item = queuefuncs.getItem(queues['filesQ'])
        # ...

# Should be
def file_handler_dispatcher(
    config: Config,
    queues: dict[str, mp.Queue],
    ...
) -> None:
    """
    Main file scanner worker process.
    
    Consumes file paths from files_queue, scans with configured data handlers,
    writes results to output-specific result queues.
    
    Args:
        config: Application configuration
        queues: Inter-process queue dict with keys:
            - 'files_queue': Input (file paths)
            - 'csv_results_queue': Output (CSV rows)
            - 'json_results_queue': Output (JSON records)
        stop_event: Shutdown signal
        log_manager: Logging manager for queue-based logging
    
    Returns:
        None (process target function)
        
    Raises:
        (Logs exceptions, doesn't raise)
    
    Example:
        >>> config = Config('config.toml')
        >>> queues = {'files_queue': mp.Queue(), ...}
        >>> proc = mp.Process(target=file_handler_dispatcher, 
        ...                     args=(config, queues, ...))
        >>> proc.start()
    """
```

**Impact**:
- New developers can't understand function purpose without reading full code
- No IDE hint popup explaining parameters
- Hard to maintain

**Refactor Solution**: All functions documented with Args, Returns, description.

---

### 3.4 Insufficient Test Coverage

**File**: `tests/` directory

**Issue**:
```
tests/
├── test_email.py (data handler tests)
├── test_pan.py (data handler tests)
├── test_read_*.py (file type tests)
└── (no process orchestration tests)
```

**Missing**:
- Unit tests for process functions
- Unit tests for queue coordination
- Integration tests for full pipeline
- Edge case tests (timeout, crash recovery)
- Baseline comparison tests

**Impact**:
- Can't refactor confidently (no regression detection)
- Bugs introduced during refactoring not caught
- Edge cases not discovered until production

**Refactor Solution**: 
- Unit tests: 100% handler coverage
- Integration tests: full scan pipeline
- E2E tests: baseline comparison
- Target: ≥ 80% coverage

---

### 3.5 Pydantic Model Gap

**File**: Data definitions scattered

**Issue**:
- Task/result definitions not centralized
- Config loading done with dict/getters, not validated models
- No schema validation or IDE support

**Impact**:
- Config typos not caught until runtime
- Data serialization unclear
- Hard to extend config

**Refactor Solution**:
- Dataclasses (or Pydantic) for Task/Result
- Explicit validation of all inputs
- Type safe throughout

---

## 4. Configuration Issues

### 4.1 Scattered Configuration Getters

**File**: `src/piidigger/classes.py` (Config class), multiple getter methods

**Issue**:
```python
# Many single-purpose getters
config.getMaxProcs()
config.getMaxFilesScanProcs()
config.getEnabledOutputTypes()
config.getLogFile()
config.getDataHandlers()
# ...
```

**Problem**:
- No clear separation: which getters are for multiprocessing? which for output?
- New settings require new getter methods
- Hard to explore what config options exist

**Impact**:
- Difficult to understand configuration structure
- Tedious to add new settings
- Unclear which settings depend on each other

**Refactor Solution**:
- Nested config structure: `config.multiprocessing`, `config.output_formats`, `config.data_handlers`
- Dataclass or dict-like access
- IDE autocomplete support

---

### 4.2 Hard-to-Find Default Values

**File**: `src/piidigger/globalfuncs.py` (getDefaultConfig function)

**Issue**:
- Default values embedded in Python function
- Not obvious to user what the defaults are
- Hard to compare custom config against defaults

**Impact**:
- Users don't know what settings affect timeout
- Difficult to debug configuration issues

**Refactor Solution**:
- Default config in TOML (user-readable)
- Well-commented with explanations
- Schema validation against defaults

---

## 5. Logging Issues

### 5.1 Queue-Based Logging Complexity

**File**: `src/piidigger/logmanager.py`

**Issue**:
- All multi-process logging goes through queue
- If logging queue blocks, entire pipeline stalls
- Difficult to debug logging itself

**Impact**:
- Occasional hangs might be logging-related (hard to distinguish)
- Adds latency to critical path

**Refactor Solution**:
- Use standard Python `multiprocessing.managers.SyncManager` logging handler (simpler)
- Or: async logging with fallback to stderr (non-blocking)

---

## 6. Edge Cases Not Handled

### 6.1 base64-xml-test.xml Catastrophic Backtracking

**File**: `testdata/pan/base64-xml-test.xml` (1.5MB)

**Issue**:
- Email regex (and possibly PAN regex) catastrophic backtracking on embedded base64 data
- Causes 2-4 minute hang when scanning with email handler enabled
- No timeout enforcement

**Impact**:
- Users report "system hung" on this specific file
- Scan timeout config doesn't help (not working)

**Refactor Solution**:
- Per-handler timeout enforcement
- Task timeout = 30 seconds
- If email handler takes >30s, log timeout and continue
- Test: base64-xml-test.xml should complete in <5 minutes total

### 6.2 Worker Process Crash

**Issue**:
- If worker process crashes, task is lost
- No retry mechanism
- No detection of lost tasks

**Impact**:
- Occasional missing results on specific files
- User doesn't know why

**Refactor Solution**:
- Task result timeout monitoring
- If result not received within 2x timeout, log error
- Potentially retry task or continue

### 6.3 Shutdown During Active Scan

**Issue**:
- Ctrl+C during middle of scan
- SENTINEL coordination complex, may not complete cleanly
- Locks not released properly

**Impact**:
- Temp files left behind
- Queue processes not fully shut down
- Next run may have lock conflicts

**Refactor Solution**:
- Explicit shutdown sequence with try/finally
- All resources cleaned up
- Graceful close of result files

---

## Summary: What the Refactor Fixes

| Issue | Current | Refactored |
|-------|---------|-----------|
| SENTINEL deadlock | Risk: hung worker breaks chain | No chain: workers independent |
| Race conditions | Counter management fragile | No manual counters |
| Load balancing | None (fixed roles) | Automatic (shared task queue) |
| Timeout enforcement | Not working (GIL + file IPC) | Process-level, reliable |
| Code quality | Inconsistent naming, no type hints | PEP8 compliant, full type hints |
| Extensibility | Difficult (touch many systems) | Easy (polymorphic handler dispatch) |
| Testing | Minimal (integration only) | Comprehensive (unit + integration + e2e) |
| Documentation | Scattered, incomplete | Complete and systematic |

---

## Related Documents

- [ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md) - Complete design proposal
- [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md) - Step-by-step tasks
