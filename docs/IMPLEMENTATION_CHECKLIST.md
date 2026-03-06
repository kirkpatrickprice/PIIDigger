# Implementation Checklist

**Branch**: `refactor`  
**Status**: Planning Phase  
**Updated**: 2026-03-06

Use this checklist to track progress through each phase of the refactor.

---

## Phase 1: Core Infrastructure

### Task & Result Definitions
- [ ] Create `src/piidigger/orchestration/tasks.py`
  - [ ] `TaskType` enum with all valid task types
  - [ ] `Task` Pydantic model with all fields
  - [ ] `TaskResult` Pydantic model with all fields
  - [ ] Validation: task_id (non-empty), timeout_seconds (1-300), task_type (enum)
  - [ ] Serialization tests (pickle compatibility, JSON round-trip)

### WorkerPool Class
- [ ] Create `src/piidigger/orchestration/worker_pool.py`
  - [ ] `WorkerPool.__init__()` - initialize queue, pool size
  - [ ] `WorkerPool.start_workers()` - spawn N worker processes
  - [ ] `WorkerPool.send_sentinel()` - shutdown sequence
  - [ ] `WorkerPool.join_all(timeout)` - wait for completion
  - [ ] Worker process function - main loop, task dispatch
  - [ ] Type hints on all methods
  - [ ] Docstrings on all classes/methods

### Timeout Executor
- [ ] Create `src/piidigger/orchestration/executor.py`
  - [ ] `execute_with_timeout()` function
  - [ ] Handle subprocess timeout enforcement
  - [ ] Handle exceptions (capture and return)
  - [ ] Proper process cleanup
  - [ ] Logger integration
  - [ ] Unit tests for timeout firing
  - [ ] Unit test for normal completion

### Module Exports
- [ ] Create `src/piidigger/orchestration/__init__.py`
  - [ ] Export `Task`, `TaskResult`, `WorkerPool`

### Phase 1 Testing
- [ ] Unit test: Task creation and field validation
- [ ] Unit test: TaskResult creation
- [ ] Unit test: WorkerPool lifecycle (start, shutdown)
- [ ] Unit test: execute_with_timeout() - normal case
- [ ] Unit test: execute_with_timeout() - timeout case
- [ ] Unit test: execute_with_timeout() - exception case
- [ ] Ruff linting: all Phase 1 files pass
- [ ] Type hints: coverage 100%

---

## Phase 2: Task Handlers

### Handler Functions (in `src/piidigger/orchestration/handlers.py`)
- [ ] `handle_enum_dirs()` - migrate from findDirsWorker
  - [ ] Accept Task with dir path list
  - [ ] Generate directory listings
  - [ ] Return TaskResult with found directories
  - [ ] Logging at info level for each batch
  - [ ] Error handling (permission denied, etc)

- [ ] `handle_enum_files()` - migrate from findFilesWorker
  - [ ] Accept Task with directory path
  - [ ] Filter by config (file extensions, MIME types)
  - [ ] Return TaskResult with found files
  - [ ] Logging at info level for each batch
  - [ ] Error handling

- [ ] `handle_scan_file()` - migrate from fileHandlerDispatcher
  - [ ] Accept Task with file path + data handlers list
  - [ ] Read and chunk file
  - [ ] Execute each data handler with timeout
  - [ ] Collect all results
  - [ ] Return TaskResult with matches
  - [ ] Logging: matches found, timeouts, errors
  - [ ] **Critical**: integrate timeout enforcement

- [ ] `handle_write_results()` - migrate from OutputHandlers
  - [ ] Accept Task with result data + output format
  - [ ] Write to file (CSV, JSON, TXT)
  - [ ] Return TaskResult with file path written
  - [ ] Error handling (file I/O errors)

### Polymorphic Dispatcher
- [ ] `execute_task(task: Task) -> TaskResult`
  - [ ] if/elif routing to handlers
  - [ ] Error handling for unknown task type
  - [ ] Timing measurement (duration_seconds)
  - [ ] Worker PID assignment

### Handler Testing
- [ ] Unit test: handle_enum_dirs with mock directory
- [ ] Unit test: handle_enum_files with test files
- [ ] Unit test: handle_scan_file with email handler
- [ ] Unit test: handle_scan_file timeout enforcement
- [ ] Unit test: handle_write_results CSV format
- [ ] Unit test: handle_write_results JSON format
- [ ] Unit test: handle_scan_file with errors
- [ ] Integration test: full pipeline on testdata/plaintext/

### Phase 2 Code Quality
- [ ] Ruff linting: handlers.py passes
- [ ] Type hints: 100% coverage
- [ ] Docstrings: all functions documented

---

## Phase 3: Orchestrator & Coordinator

### TaskCoordinator Class
- [ ] Create class in `src/piidigger/orchestration/coordinator.py`
  - [ ] `TaskCoordinator.__init__(config, queue, logger)`
  - [ ] `TaskCoordinator.generate_enum_dirs_tasks()`
  - [ ] `TaskCoordinator.generate_enum_files_tasks(dir_result)`
  - [ ] `TaskCoordinator.generate_scan_file_tasks(file_result)`
  - [ ] `TaskCoordinator.generate_write_result_tasks(scan_result)`
  - [ ] Task ID generation (UUID)
  - [ ] Priority assignment (if applicable)

### Main Orchestration Loop
- [ ] Refactor `src/piidigger/piidigger.py::main()`
  - [ ] Create task_queue, result_queue
  - [ ] Create worker pool
  - [ ] Instantiate TaskCoordinator
  - [ ] Main loop: coordinator generates → workers execute → collect results
  - [ ] Progress tracking integration
  - [ ] Totals tracking integration
  - [ ] Graceful shutdown on Ctrl+C
  - [ ] Graceful shutdown on stop_event

### Shutdown Sequence
- [ ] `shutdown_graceful(pool, coordinator, stop_event)`
  - [ ] Set stop_event
  - [ ] Wait for coordinator (timeout 10s)
  - [ ] Send SENTINEL to all workers
  - [ ] Join workers (timeout 30s)
  - [ ] Close queues
  - [ ] Cleanup temp files (if any)
  - [ ] Log final statistics

### Existing Code Changes
- [ ] Remove old ProcessManager usage (or deprecate)
- [ ] Remove old queue coordination logic
- [ ] Update logging to use new logger pattern
- [ ] Update config.py to reflect new settings (if needed)

### Phase 3 Testing
- [ ] Integration test: coordinator → workers → results
- [ ] Integration test: timeout enforcement during scan
- [ ] Integration test: graceful shutdown mid-scan
- [ ] Integration test: empty directory handling
- [ ] Integration test: file with 0 matches
- [ ] Integration test: results in all output formats
- [ ] E2E test: full scan on testdata/ directory

### Phase 3 Code Quality
- [ ] Ruff linting: piidigger.py passes
- [ ] Type hints: 100% coverage
- [ ] Docstrings: all functions documented

---

## Phase 4: Code Quality & Standards

### Naming Conventions Audit
- [ ] Run: `grep -r "dirsQ\|filesQ\|resultQ" src/`
- [ ] Replace all abbreviated queue names with real_names
- [ ] Verify all variable names use underscores (not camelCase unless class names)
- [ ] Verify all class names use PascalCase
- [ ] Verify all constants use UPPER_CASE

### Type Hints Coverage
- [ ] Run: `mypy src/piidigger/ --strict` (or similar)
- [ ] Ensure all function signatures have type hints
- [ ] Ensure all return types are declared
- [ ] Use `Optional[T]` or `T | None` consistently
- [ ] Use `list[T]` not `List[T]` (Python 3.9+)

### Documentation
- [ ] Module docstrings: all modules documented
- [ ] Class docstrings: all classes documented
- [ ] Function docstrings: all functions documented
  - [ ] Include Args, Returns, Raises sections
  - [ ] Include usage examples where non-obvious
- [ ] Inline comments: for non-obvious logic only (not obvious code)

### Linting & Formatting
- [ ] Run: `ruff check . --select E,W,F,I,UP,RUF`
  - [ ] Fix all violations
  - [ ] Commit fixes
- [ ] Run: `ruff format .`
  - [ ] Review diffs
  - [ ] Commit formatting

### Remove Old Patterns
- [ ] Delete ProcessManager class (if not used elsewhere)
- [ ] Delete old queue coordination functions
- [ ] Delete old handler dispatch logic (replaced by polymorphic handlers)

### Phase 4 Testing
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Check coverage: `pytest --cov=src/piidigger tests/`
  - [ ] Target: ≥ 80% coverage
- [ ] Run linter: `ruff check .`
  - [ ] Target: 0 violations
- [ ] Run formatter: `ruff format --check .`
  - [ ] Target: 0 diffs

---

## Phase 5: Backward Compatibility & Validation

### Output Format Validation
- [ ] [ ] Run baseline scan: `uv run piidigger -f piidigger.toml`
  - [ ] Generate CSV, JSON, TXT outputs
  - [ ] Save as baseline_output/
- [ ] Run refactored version on same directory
  - [ ] Generate outputs to refactored_output/
- [ ] Compare files (byte-for-byte or semantic diff)
  - [ ] CSV: row count, column order matches
  - [ ] JSON: same keys, same match content
  - [ ] TXT: same line count and content

### Edge Case Testing
- [ ] Test: base64-xml-test.xml completes in <5 minutes
  - [ ] With email handler and 30s timeout
  - [ ] Verify no hangs in log
  - [ ] Verify partial results logged
- [ ] Test: empty directory scan
  - [ ] Should complete without errors
- [ ] Test: directory with no PII
  - [ ] Should complete, 0 results
- [ ] Test: file with 1000+ matches
  - [ ] Memory stable during processing
- [ ] Test: file access denied
  - [ ] Error logged, scan continues
- [ ] Test: worker crash (synthetic)
  - [ ] System detects and recovers (or logs cleanly)

### Performance Validation
- [ ] Time baseline: original version on testdata/
  - [ ] Record wall-clock time
- [ ] Time refactored: new version on testdata/
  - [ ] Record wall-clock time
  - [ ] Compare (should be within 10-20% for equivalent work)

### Final Sign-Off
- [ ] [ ] All tests passing
- [ ] [ ] Ruff: 0 violations
- [ ] [ ] Coverage: ≥ 80%
- [ ] [ ] Documentation complete
- [ ] [ ] Edge cases validated
- [ ] [ ] Output formats match baseline
- [ ] [ ] Code review approved

---

## Notes & Questions

### Open Questions
- Q1: Should we introduce `async/await` with `asyncio` for I/O operations?
  - **Decision**: No - `multiprocessing` simpler for current scope, `asyncio` adds complexity without solving CPU-bound regex issue.

- Q2: Pydantic models for Task/TaskResult validation?
  - **Decision**: **YES - Use Pydantic v2**. IPC validation critical for multiprocessing queues. Pydantic v2 has zero transitive dependencies. Justifies 1 package addition for robust validation on task deserialization.

- Q3: How to handle per-handler timeouts (email=30s, pan=10s)?
  - **Decision**: Task model includes timeout_seconds, config provides defaults by handler type.

### Known Risks
- **Risk 1**: Full orchestration rewrite → high test coverage needed before deployment
  - **Mitigation**: Comprehensive integration/e2e tests before main branch merge
  
- **Risk 2**: Existing deployments expect output format
  - **Mitigation**: Output format preserved exactly, full baseline comparison

- **Risk 3**: Team unfamiliar with new architecture
  - **Mitigation**: Comprehensive documentation + code comments

---

## Related Documents

- [ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md) - Full design rationale
- [docs/CURRENT_ISSUES.md](./CURRENT_ISSUES.md) - Detailed problem analysis
- [docs/TESTING_PLAN.md](./TESTING_PLAN.md) - Comprehensive test strategy
