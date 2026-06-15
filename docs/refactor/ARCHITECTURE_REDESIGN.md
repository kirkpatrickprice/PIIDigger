# PIIDigger Architecture Redesign

**Branch**: `refactor`  
**Status**: Design Phase  
**Last Updated**: 2026-03-06

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [Problem Statement](#problem-statement)
3. [Current Architecture Analysis](#current-architecture-analysis)
4. [Proposed Architecture](#proposed-architecture)
5. [Design Patterns](#design-patterns)
6. [Implementation Strategy](#implementation-strategy)
7. [Code Quality Standards](#code-quality-standards)
8. [Testing Strategy](#testing-strategy)
9. [Known Edge Cases](#known-edge-cases)

---

## Executive Summary

PIIDigger currently uses a **tightly-coupled, SENTINEL-based multiprocessing architecture** that:
- Breaks when new process types are introduced (attempted fileScanner parallelization failed)
- Causes cascading failures and occasional hangs due to manual queue coordination
- Cannot be easily tested, extended, or maintained
- Does not meet modern Python standards (naming conventions, type hints, documentation, linting)

**Proposed Solution**: Replace with a **Task Queue + Worker Pool pattern** using:
- Polymorphic task objects (single source of truth for work)
- Generic worker pool (no role specialization)
- Clean message-passing IPC (no SENTINEL hacks)
- Configurable timeouts and retry logic
- Full Ruff compliance and comprehensive Pytest coverage

**Scope**: Complete architectural refactor touching all multiprocessing infrastructure, achieving production-ready code quality in the process.

---

## Problem Statement

### Root Cause Analysis

The current `ProcessManager` class implements a **rigid, role-specialized process orchestration**:

```
findDirsWorker(1)  →  dirsQ  →  findFilesWorker(1)  →  filesQ  →  fileHandlers(N)  →  resultsQ  →  OutputHandlers
```

**Problems with this design:**

1. **SENTINEL-Based Coordination Brittleness**
   - Manual `put(SENTINEL)` signals between processes
   - If any process exits early, chain breaks (deadlock or cascade failure)
   - `activeFilesQProcesses` counter has race conditions on decrement logic
   - No automatic retry mechanism - lost signals = hung system

2. **Inability to Extend**
   - Attempted to add parallel fileScanner (rebalance idle workers) → **FAILED**
   - New process type requires modifying:
     - ProcessManager registration
     - Queue creation
     - SENTINEL coordination logic
     - Counter management
     - Shutdown choreography
   - Every change risks breaking the fragile handoff chain

3. **Load Imbalance**
   - Fixed role specialization: 1 dir scanner → 1 file scanner → N file processors
   - If dir enumerating is slow, filesQ starves while N processors idle
   - If file processing is slow, filesQ fills while scanner blocks
   - No dynamic rebalancing or task redistribution

4. **Timeout Mechanism Failure**
   - Attempted threading-based timeout → blocked by GIL
   - Attempted multiprocessing timeout → IPC complexity (pickling, file passing)
   - Root cause: Single fileHandler process hanging → blocks next SENTINEL signal → cascade failure
   - Timeout enforcement isolated to single queue while others use SENTINEL hacks

5. **Code Quality**
   - Naming: `dirsQ`, `filesQ` (abbrev) instead of `dirs_queue`, `files_queue` (PEP8)
   - No type hints, minimal docstrings
   - Process functions scattered across modules with no clear interface
   - No async context managers or resource cleanup
   - Inadequate test coverage (integration tests only, no unit tests for process logic)

---

## Current Architecture Analysis

### Process Hierarchy

```
ProcessManager (custom class)
├── LoggerPM
│   └── logProcessor (1)
├── MainPM
│   ├── findDirsWorker (1)
│   ├── findFilesWorker (1)
│   ├── fileHandler (cpu_count)
│   └── OutputHandlers (1+ per type)
└── progressPM
    └── progressLineWorker (1)
```

### Queue System

| Queue | Source | Consumer | Coordination |
|-------|--------|----------|---------------|
| `logQ` | All workers | logProcessor | Direct put() |
| `dirsQ` | findDirsWorker | findFilesWorker | SENTINEL on empty |
| `filesQ` | findFilesWorker | fileHandler | SENTINEL + activeFilesQProcesses counter |
| `totalsQ` | All workers | Statistics aggregator | Direct put() |
| `{output}_resultsQ` | fileHandler | OutputHandlers | SENTINEL per output type |

### Fragile Handoff (fileHandler Example)

```python
# lines ~210-229 in piidigger.py
while True:
    item = getItem(queues['filesQ'])  # Wait for work
    if item is SENTINEL:
        # Check counter and decide if last worker
        with activeFilesQProcesses.get_lock():
            activeFilesQProcesses.value -= 1
            if activeFilesQProcesses.value == 0:
                clearQ(queues['filesQ'])
            else:
                queues['filesQ'].put(SENTINEL)  # Pass signal to next worker
        break
    # Process item...
```

**Why this fails:**
- If file processing hangs (e.g., email regex on base64-xml-test.xml), worker never reaches the decrement
- Counter gets out of sync
- Next worker waiting on filesQ.get() never receives SENTINEL
- Entire pipeline deadlocks while hung worker consumes CPU

---

## Proposed Architecture

### Core Pattern: Task Queue + Worker Pool

**Central Concept**: Work is represented as **immutable task messages** consumed by **generic workers**.

### Task Model (Pydantic v2)

```python
from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum
from datetime import datetime
from typing import Literal

class TaskType(str, Enum):
    """Valid task types for worker dispatch."""
    ENUM_DIRS = "enum_dirs"
    ENUM_FILES = "enum_files"
    SCAN_FILE = "scan_file"
    WRITE_RESULTS = "write_results"

class Task(BaseModel):
    """Immutable task definition for worker consumption.
    
    Pydantic v2 validation ensures tasks deserialized from multiprocessing
    queues are valid before processing. Frozen=True prevents accidental mutation.
    """
    
    model_config = ConfigDict(frozen=True)
    
    task_id: str
    task_type: TaskType  # Enum validation
    payload: dict
    timeout_seconds: int = Field(default=30, ge=1, le=300)  # Range validation
    priority: int = Field(default=0, ge=0, le=10)
    created_at: datetime = Field(default_factory=datetime.now)
    
    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, v: str) -> str:
        """Ensure task_id is non-empty UUID-like string."""
        if not v or len(v) < 8:
            raise ValueError("task_id must be non-empty, UUID-like string (min 8 chars)")
        return v

class TaskResult(BaseModel):
    """Result of task execution.
    
    Pydantic validation ensures result data is structurally sound
    before aggregation or output processing.
    """
    
    task_id: str
    task_type: TaskType
    status: Literal["success", "timeout", "error"]
    result_data: dict | None = None
    error_message: str | None = None
    duration_seconds: float = Field(ge=0.0)
    worker_pid: int | None = None
```

### Worker Function

Single polymorphic worker handles all task types:

```python
def worker_process(
    task_queue: mp.Queue,
    result_queue: mp.Queue,
    config: Config,
    logger: Logger
):
    """
    Generic worker: polls task queue, routes by task_type, executes with timeout.
    
    Handles:
    - Invalid tasks (log and continue)
    - Timeouts (log and mark as timeout, continue)
    - Exceptions (log, mark as error, continue)
    - Worker lifecycle cleanup on exit
    """
    while True:
        task = task_queue.get()
        if task is SENTINEL:
            break
        
        try:
            result = execute_task(task, config, logger)
            result_queue.put(result)
        except Exception as e:
            result_queue.put(TaskResult(
                task_id=task.task_id,
                task_type=task.task_type,
                status="error",
                error_message=str(e),
                worker_pid=os.getpid()
            ))
```

### Orchestrator: Task Coordinator

Single coordinator process generates tasks for workers:

```python
def task_coordinator(
    task_queue: mp.Queue,
    result_queue: mp.Queue,
    config: Config,
    logger: Logger,
    stop_event: mp.Event
):
    """
    Single coordinator: generates scanning tasks, feeds worker pool, collects results.
    
    Flow:
    1. Generate "enum_dirs" tasks (directory paths)
    2. Track results, generate "enum_files" tasks when dirs complete
    3. Generate "scan_file" tasks when files discovered
    4. Feed results to output processors
    5. Graceful shutdown on stop_event
    """
```

### Worker Pool & Configuration

```python
# config.toml
[multiprocessing]
file_handler_workers = 8        # cpu_count() by default
coordinator_workers = 1         # Fixed (single coordinator)
output_workers = 2              # Configurable per output type

# Runtime
pool = WorkerPool(
    pool_size=config.file_handler_workers,
    task_queue=task_queue,
    result_queue=result_queue,
    config=config,
    logger=logger
)
pool.start_workers()
```

---

## Design Patterns

### 1. Polymorphic Task Dispatch

**Pattern**: Single worker function with if/elif routing.

```python
def execute_task(task: Task, config: Config, logger: Logger) -> TaskResult:
    """Route task to handler. Single point of extension."""
    
    if task.task_type == "enum_dirs":
        return handle_enum_dirs(task, config, logger)
    
    elif task.task_type == "enum_files":
        return handle_enum_files(task, config, logger)
    
    elif task.task_type == "scan_file":
        return handle_scan_file(task, config, logger)
    
    elif task.task_type == "write_results":
        return handle_write_results(task, config, logger)
    
    else:
        raise ValueError(f"Unknown task type: {task.task_type}")
```

**Advantage**: Adding new task type = add elif branch + handler function. No ProcessManager changes.

### 2. Timeout Enforcement

**Pattern**: Subprocess isolation with process-level termination.

```python
def execute_with_timeout(
    target_func,
    args: tuple,
    timeout_seconds: int,
    logger: Logger
) -> tuple[bool, object]:
    """
    Execute function in subprocess, enforce hard timeout via process termination.
    
    Returns: (success: bool, result: object | Exception)
    """
    with mp.Pool(1) as pool:
        try:
            result = pool.apply_async(target_func, args)
            return True, result.get(timeout=timeout_seconds)
        except mp.TimeoutError:
            logger.warning(f"Task timeout after {timeout_seconds}s")
            return False, TimeoutError(f"Exceeded {timeout_seconds}s")
        except Exception as e:
            logger.error(f"Task failed: {e}")
            return False, e
```

**Advantage**: GIL not involved. Process can be forcefully terminated. No blocking in main thread.

### 3. Result Tracking

**Pattern**: Task ID → Task, Task → Result correlation.

```python
# Coordinator maintains in-memory tracking during scan
pending_tasks: dict[str, Task] = {}
completed_tasks: dict[str, TaskResult] = {}

# Collect results
while True:
    result = result_queue.get_nowait()
    completed_tasks[result.task_id] = result
    logger.info(f"Task {result.task_id} ({result.task_type}) completed in {result.duration_seconds}s")
```

**Advantage**: Can correlate work → output, detect lost tasks, implement retry logic.

### 4. Graceful Shutdown

**Pattern**: Stop event + SENTINEL cascade.

```python
def shutdown_sequence(pool: WorkerPool, coordinator: mp.Process, stop_event: mp.Event):
    """
    1. Signal stop event (no new tasks)
    2. Wait for coordinator to finish current batch
    3. Send SENTINEL to all workers
    4. Join workers
    5. Cleanup
    """
    stop_event.set()
    coordinator.join(timeout=10)
    
    for _ in range(pool.worker_count):
        pool.task_queue.put(SENTINEL)
    
    pool.join_all(timeout=30)
```

---

## Implementation Strategy

### Phase 1: Core Infrastructure (Foundation)

**Deliverables**:
- `Task` and `TaskResult` Pydantic v2 models
- `WorkerPool` class (spawn/manage workers)
- `task_executor.execute_with_timeout()` function
- Initial worker function skeleton

**Files to Create**:
- `src/piidigger/orchestration/tasks.py` (Task definitions)
- `src/piidigger/orchestration/worker_pool.py` (Worker management)
- `src/piidigger/orchestration/executor.py` (Timeout enforcement)

**Files to Modify**:
- `src/piidigger/__init__.py` (exports)

**Testing**:
- Unit tests for Task creation and serialization
- Unit tests for timeout enforcement
- Integration test: spin up pool, send 10 tasks, verify results

---

### Phase 2: Task Handlers (Business Logic)

**Deliverables**:
- `handle_enum_dirs()` - migrate findDirsWorker logic
- `handle_enum_files()` - migrate findFilesWorker logic
- `handle_scan_file()` - migrate fileHandlerDispatcher logic (with timeout)
- `handle_write_results()` - migrate OutputHandler logic

**Files to Create**:
- `src/piidigger/orchestration/handlers.py`
- `src/piidigger/orchestration/__init__.py`

**Testing**:
- Unit test each handler with mock task payloads
- Integration test: full scan on small test directory

---

### Phase 3: Orchestrator & Coordinator

**Deliverables**:
- `TaskCoordinator` class - generates tasks from scan config
- Main orchestration loop in `main()`
- Shutdown handling

**Files to Modify**:
- `src/piidigger/piidigger.py` (replace main orchestration)

**Testing**:
- Integration test: coordinator generates tasks, workers execute, results collected
- Edge case: directory with 0 files
- Edge case: timeout on scan_file task

---

### Phase 4: Code Quality & Testing

**Deliverables**:
- Full Ruff compliance (linting, formatting)
- Type hints on all functions
- Docstrings (module, class, function)
- Pytest coverage ≥ 80%
- Remove old ProcessManager class (if no longer used)

**Files to Review**:
- All modified files for naming (real_names_with_underbars)
- All handlers for type hints and docstrings
- Test structure (unit/integration/e2e clear separation)

**Testing**:
- `pytest --cov=src/piidigger tests/`
- `ruff check .`
- `ruff format --check .`

---

### Phase 5: Backward Compatibility Validation

**Deliverables**:
- Run on existing test datasets
- Verify output formats match (CSV, JSON, TXT)
- Compare results against baseline scans

**Files to Modify**:
- (none - output format preserved)

**Testing**:
- Integration test: scan with all output types enabled
- Compare file-by-file with previous runs
- Edge case: base64-xml-test.xml (previously caused hangs)

---

## Code Quality Standards

### Naming Conventions

- **Variables/Functions**: `real_names_with_underbars` (PEP8)
  - ✗ `dirsQ`, `filesQ`, `resultQ`
  - ✓ `dirs_queue`, `files_queue`, `result_queue`
  
- **Classes**: `PascalCase`
  - ✗ `config`, `processor`
  - ✓ `Config`, `TaskCoordinator`

- **Constants**: `UPPER_CASE`
  - ✗ `SENTINEL`, `TIMEOUT_DEFAULT`
  - ✓ `QUEUE_SENTINEL`, `TIMEOUT_DEFAULT_SECONDS`

### Type Hints

**Required everywhere** (except simple test functions):

```python
def scan_file(
    filepath: Path,
    handlers: list[DataHandler],
    timeout_seconds: int = 30,
    logger: Logger | None = None
) -> dict[str, list[Match]]:
    """Scan file with handlers, return matches by handler name."""
```

### Documentation

**Required**:
- Module docstring (purpose, key exports)
- Class docstring (purpose, usage example if complex)
- Function docstring (Args, Returns, Raises, Examples where appropriate)
- Inline comments for non-obvious logic

### Linting

**Target**: 100% Ruff compliance

```bash
ruff check . --select E,W,F,I,UP,RUF
ruff format .
```

---

## Testing Strategy

### Unit Tests

**Coverage Target**: ≥ 80%

**Structure**:
```
tests/
├── unit/
│   ├── test_tasks.py (Task creation, validation)
│   ├── test_worker_pool.py (Pool lifecycle)
│   ├── test_executor.py (Timeout enforcement)
│   ├── test_handlers.py (Each handler in isolation)
│   └── test_config.py (Config parsing, validation)
├── integration/
│   ├── test_scan_small_directory.py
│   ├── test_timeout_enforcement.py
│   ├── test_output_formats.py
│   └── test_shutdown_graceful.py
└── e2e/
    ├── test_scan_full_testdata.py
    └── test_baseline_comparison.py
```

### Test Fixtures

```python
@pytest.fixture
def task_queue():
    """mp.Queue for testing."""
    q = mp.Queue()
    yield q
    while not q.empty():
        q.get()

@pytest.fixture
def sample_config():
    """Config with test defaults."""
    return Config.from_dict({
        "start_dirs": ["testdata/"],
        "file_handlers": ["pan", "email"],
        "output_formats": ["csv"],
    })
```

### Edge Case Tests

- Empty directory scan
- File with no matches
- File with 1000+ matches (memory pressure)
- Binary file (encoding edge cases)
- base64-xml-test.xml (previously hung system)
- Very deep directory structure (stack depth?)
- File access denied (permission handling)
- Timeout mid-scan (graceful exit)
- Worker crash (error handling)

---

## Known Edge Cases

### 1. base64-xml-test.xml Catastrophic Backtracking

**Current Problem**: Email regex hangs for 2-4 minutes on this 1.5MB file with embedded base64 data.

**Root Cause**: Regex catastrophic backtracking in RFC5322 pattern when matching partial email-like strings in base64 payload.

**Solution**:
- Timeout per task: if email handler takes >30s, terminate and continue
- Log timeout (not silent failure)
- Process continues (no cascade failure)

**Validation**: base64-xml-test.xml should complete in <5 minutes with email timeout=30s.

### 2. Process Pool Worker Crashes

**Risk**: Worker process crashes → task lost → queue blocks waiting for result.

**Solution**:
- TaskResult timestamp + timeout monitoring
- If result_queue empty for X seconds, assume worker crashed
- Resend SENTINEL or restart worker
- Log incident with task_id for tracing

**Validation**: Inject synthetic crash, verify recovery.

### 3. Shutdown Deadlock

**Risk**: Coordinator waiting for result_queue while workers blocked on task_queue (circular wait).

**Solution**:
- stop_event checked in coordinator loop (unblock gather phase)
- All workers receive SENTINEL (unblock consumption)
- Timeout on final joins (don't hang forever on cleanup)

**Validation**: Graceful shutdown tests with partial task completion.

---

## Implementation Roadmap

| Phase | Priority | Effort | Risk | Timeline |
|-------|----------|--------|------|----------|
| 1: Core Infrastructure | P0 | 2-3 days | Low | Week 1 |
| 2: Task Handlers | P0 | 3-4 days | Medium | Week 2 |
| 3: Orchestrator | P0 | 2-3 days | High | Week 2-3 |
| 4: Code Quality | P0 | 2-3 days | Low | Week 3 |
| 5: Validation | P1 | 1-2 days | Medium | Week 3-4 |

**Total Estimate**: 3-4 weeks for full refactor + validation.

---

## Success Criteria

- [ ] All tests pass (unit, integration, e2e)
- [ ] Ruff linting: 0 violations
- [ ] Type hint coverage: 100%
- [ ] Test coverage: ≥ 80%
- [ ] base64-xml-test.xml completes in <5 minutes
- [ ] Output formats match baseline (CSV, JSON, TXT identical)
- [ ] No SENTINEL timeout errors in logs
- [ ] Graceful shutdown on Ctrl+C
- [ ] Code review: approved by project maintainer
