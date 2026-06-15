# Testing Strategy for Architecture Refactor

**Branch**: `refactor`  
**Status**: Planning Document  
**Last Updated**: 2026-03-06

---

## Overview

The refactor involves rewriting the entire process orchestration system. This requires a **comprehensive, multi-layered testing approach** to ensure correctness and prevent regressions.

**Testing Philosophy**:
- **Unit Tests**: Each component in isolation
- **Integration Tests**: Components working together
- **E2E Tests**: Full pipeline on real data
- **Baseline Comparison**: Output matches original implementation

---

## Test Structure

```
tests/
├── unit/
│   ├── conftest.py (shared fixtures)
│   ├── orchestration/
│   │   ├── test_tasks.py
│   │   ├── test_worker_pool.py
│   │   ├── test_executor.py
│   │   ├── test_handlers.py
│   │   └── test_coordinator.py
│   ├── test_datahandlers.py (existing - preserve)
│   ├── test_filetype_handlers.py (existing - preserve)
│   └── test_config.py
├── integration/
│   ├── conftest.py (shared fixtures)
│   ├── test_full_scan_small.py (small test directory)
│   ├── test_timeout_enforcement.py
│   ├── test_graceful_shutdown.py
│   ├── test_output_formats.py
│   ├── test_edge_cases.py
│   └── test_result_accuracy.py
├── e2e/
│   ├── conftest.py (shared fixtures)
│   ├── test_baseline_comparison.py (compare against original)
│   └── test_full_scan_testdata.py (complete testdata/ directory)
└── fixtures/
    ├── sample_config.toml
    ├── sample_data/ (small test files)
    └── baseline_results/ (reference output)
```

---

## Unit Tests

### 1. Task and TaskResult Definition Tests

**File**: `tests/unit/orchestration/test_tasks.py`

**Tests**:
```python
def test_task_creation():
    """Task can be created with required fields."""
    task = Task(
        task_id="task-123",
        task_type="scan_file",
        payload={"file": "test.txt"},
        timeout_seconds=30
    )
    assert task.task_id == "task-123"
    assert task.task_type == "scan_file"

def test_task_frozen():
    """Task is immutable (Pydantic frozen model)."""
    task = Task(task_id="t1", task_type="enum_dirs", payload={})
    with pytest.raises(ValidationError):
        task.task_id = "t2"

def test_task_serialization_pickle():
    """Task can be pickled (for multiprocessing)."""
    task = Task(task_id="t1", task_type="scan", payload={"x": 1})
    pickled = pickle.dumps(task)
    restored = pickle.loads(pickled)
    assert restored.task_id == "t1"

def test_taskresult_creation():
    """TaskResult can be created with various status values."""
    result = TaskResult(
        task_id="t1",
        task_type="scan",
        status="success",
        result_data={"matches": []},
        duration_seconds=1.5
    )
    assert result.status == "success"
    assert result.duration_seconds == 1.5

def test_taskresult_error_fields():
    """TaskResult captures error information."""
    result = TaskResult(
        task_id="t1",
        task_type="scan",
        status="error",
        error_message="File not found"
    )
    assert result.status == "error"
    assert result.error_message == "File not found"

def test_taskresult_timeout_status():
    """TaskResult can represent timeout."""
    result = TaskResult(
        task_id="t1",
        task_type="scan_file",
        status="timeout",
        duration_seconds=30.0
    )
    assert result.status == "timeout"

def test_task_validation_rejects_invalid_timeout():
    """Pydantic validates timeout_seconds range (1-300)."""
    with pytest.raises(ValidationError) as exc_info:
        Task(task_id="t1", task_type="enum_dirs", payload={}, timeout_seconds=-1)
    assert "timeout_seconds" in str(exc_info.value)

def test_task_validation_rejects_invalid_task_type():
    """Pydantic validates task_type is known enum value."""
    with pytest.raises(ValidationError) as exc_info:
        Task(task_id="t1", task_type="invalid_type", payload={})
    assert "task_type" in str(exc_info.value)

def test_task_validation_rejects_short_task_id():
    """Pydantic validates task_id is UUID-like (min 8 chars)."""
    with pytest.raises(ValidationError) as exc_info:
        Task(task_id="short", task_type="scan_file", payload={})
    assert "task_id" in str(exc_info.value)

def test_taskresult_validation_rejects_invalid_status():
    """Pydantic validates status is one of allowed literals."""
    with pytest.raises(ValidationError) as exc_info:
        TaskResult(task_id="t1", task_type="scan_file", 
                  status="pending", duration_seconds=0)
    assert "status" in str(exc_info.value)

def test_task_model_json_schema():
    """Pydantic can generate JSON schema for documentation."""
    schema = Task.model_json_schema()
    assert "properties" in schema
    assert "task_id" in schema["properties"]
    assert schema["properties"]["timeout_seconds"]["minimum"] == 1
    assert schema["properties"]["timeout_seconds"]["maximum"] == 300
```
```

---

### 2. WorkerPool Lifecycle Tests

**File**: `tests/unit/orchestration/test_worker_pool.py`

**Tests**:
```python
def test_worker_pool_init():
    """WorkerPool initializes with correct parameters."""
    pool = WorkerPool(
        pool_size=4,
        task_queue=mp.Queue(),
        result_queue=mp.Queue(),
        config=sample_config
    )
    assert pool.pool_size == 4
    assert pool.worker_count == 0  # Not started yet

def test_worker_pool_start():
    """WorkerPool spawns workers on start()."""
    pool = WorkerPool(pool_size=4, task_queue=..., result_queue=...)
    pool.start_workers()
    assert pool.worker_count == 4
    for proc in pool.workers:
        assert proc.is_alive()

def test_worker_pool_shutdown():
    """WorkerPool shuts down cleanly."""
    pool = WorkerPool(pool_size=2, ...)
    pool.start_workers()
    pool.send_sentinel()  # Send shutdown signal
    pool.join_all(timeout=5)
    assert not any(p.is_alive() for p in pool.workers)

def test_worker_pool_join_timeout():
    """WorkerPool respects join timeout."""
    pool = WorkerPool(pool_size=1, ...)
    # Don't send sentinel (workers never exit)
    start = time.time()
    pool.join_all(timeout=1)
    elapsed = time.time() - start
    assert 0.9 < elapsed < 1.5  # ~1 second, allowing variance
```

---

### 3. Timeout Executor Tests

**File**: `tests/unit/orchestration/test_executor.py`

**Tests**:
```python
def test_execute_with_timeout_success():
    """Function completes before timeout."""
    def quick_task():
        return 42
    
    success, result = execute_with_timeout(quick_task, args=(), timeout_seconds=5)
    assert success is True
    assert result == 42

def test_execute_with_timeout_timeout():
    """Function times out after specified duration."""
    def slow_task():
        time.sleep(10)
        return 42
    
    start = time.time()
    success, result = execute_with_timeout(slow_task, args=(), timeout_seconds=1)
    elapsed = time.time() - start
    
    assert success is False
    assert isinstance(result, TimeoutError)
    assert 0.9 < elapsed < 1.5  # ~1 second

def test_execute_with_timeout_exception():
    """Function raises exception during execution."""
    def error_task():
        raise ValueError("Test error")
    
    success, result = execute_with_timeout(error_task, args=(), timeout_seconds=5)
    assert success is False
    assert isinstance(result, ValueError)
    assert str(result) == "Test error"

def test_execute_with_timeout_cpu_bound():
    """Timeout works on CPU-bound (regex) operations."""
    def cpu_bound_task():
        # Simulated catastrophic backtracking
        import re
        pattern = "(a+)+" * 10
        text = "a" * 25 + "X"
        re.match(pattern, text)
    
    success, result = execute_with_timeout(cpu_bound_task, args=(), timeout_seconds=1)
    assert success is False
    assert isinstance(result, TimeoutError)
```

---

### 4. Handler Function Tests

**File**: `tests/unit/orchestration/test_handlers.py`

**Tests**:
```python
def test_handle_enum_dirs():
    """handle_enum_dirs generates file paths for subdirectories."""
    task = Task(
        task_id="enum-1",
        task_type="enum_dirs",
        payload={"base_path": str(TEST_DATA_DIR)},
        timeout_seconds=10
    )
    
    config = Config.from_dict(SAMPLE_CONFIG)
    result = handlers.handle_enum_dirs(task, config)
    
    assert result.status == "success"
    assert len(result.result_data["directories"]) > 0
    assert result.duration_seconds > 0

def test_handle_enum_files():
    """handle_enum_files discovers files matching config criteria."""
    task = Task(
        task_id="enum-2",
        task_type="enum_files",
        payload={"dir_path": str(TEST_DATA_DIR / "plaintext")},
        timeout_seconds=10
    )
    
    config = Config.from_dict(SAMPLE_CONFIG)  # has .txt, .csv enabled
    result = handlers.handle_enum_files(task, config)
    
    assert result.status == "success"
    assert len(result.result_data["files"]) > 0
    # All returned files should match configured extensions
    for file_info in result.result_data["files"]:
        assert file_info["path"].suffix in [".txt", ".csv"]

def test_handle_scan_file():
    """handle_scan_file scans file with data handlers."""
    task = Task(
        task_id="scan-1",
        task_type="scan_file",
        payload={
            "file_path": str(TEST_DATA_DIR / "plaintext" / "test.txt"),
            "handlers": ["pan", "email"]
        },
        timeout_seconds=10
    )
    
    config = Config.from_dict(SAMPLE_CONFIG)
    result = handlers.handle_scan_file(task, config)
    
    assert result.status in ("success", "timeout")  # Both acceptable
    assert "pan_matches" in result.result_data or result.status == "timeout"

def test_handle_scan_file_timeout():
    """handle_scan_file respects timeout on hung regex."""
    # Use base64-xml-test.xml which causes catastrophic backtracking
    task = Task(
        task_id="scan-2",
        task_type="scan_file",
        payload={
            "file_path": str(TEST_DATA_DIR / "pan" / "base64-xml-test.xml"),
            "handlers": ["email"]
        },
        timeout_seconds=5  # Should timeout
    )
    
    config = Config.from_dict(SAMPLE_CONFIG)
    result = handlers.handle_scan_file(task, config)
    
    assert result.status == "timeout"
    assert result.duration_seconds >= 4.9  # Close to 5 second timeout

def test_handle_write_results_csv():
    """handle_write_results writes CSV output."""
    output_file = tmp_path / "results.csv"
    
    task = Task(
        task_id="write-1",
        task_type="write_results",
        payload={
            "output_file": str(output_file),
            "format": "csv",
            "results": [
                {"file": "test.txt", "handler": "pan", "match": "4111111111111111"},
                {"file": "test.txt", "handler": "email", "match": "user@example.com"}
            ]
        },
        timeout_seconds=10
    )
    
    config = Config.from_dict(SAMPLE_CONFIG)
    result = handlers.handle_write_results(task, config)
    
    assert result.status == "success"
    assert output_file.exists()
    assert output_file.stat().st_size > 0
    # Verify CSV content
    with open(output_file) as f:
        lines = f.readlines()
        assert len(lines) == 3  # header + 2 records
```

---

## Integration Tests

### 1. Full Scan Pipeline (Small Dataset)

**File**: `tests/integration/test_full_scan_small.py`

**Tests**:
```python
def test_scan_small_directory(tmp_path):
    """Full scan pipeline on small test directory."""
    # Setup
    config = Config.from_dict({
        "start_dirs": [str(SMALL_TEST_DATA_DIR)],
        "file_handlers": ["pan", "email"],
        "output_formats": ["csv", "json"],
        "output_dir": str(tmp_path)
    })
    
    # Execute
    exit_code = run_main(config)
    
    # Verify
    assert exit_code == 0
    assert (tmp_path / "results.csv").exists()
    assert (tmp_path / "results.json").exists()
    
    # Check content
    with open(tmp_path / "results.csv") as f:
        csv_lines = f.readlines()
        assert len(csv_lines) > 1  # header + data
    
    with open(tmp_path / "results.json") as f:
        json_data = json.load(f)
        assert "results" in json_data
        assert len(json_data["results"]) > 0

def test_scan_with_output_types(tmp_path):
    """Scan produces all requested output formats."""
    config = Config.from_dict({
        "start_dirs": [str(SMALL_TEST_DATA_DIR)],
        "file_handlers": ["pan"],
        "output_formats": ["csv", "json", "txt"],
        "output_dir": str(tmp_path)
    })
    
    exit_code = run_main(config)
    
    assert exit_code == 0
    assert (tmp_path / "results.csv").exists()
    assert (tmp_path / "results.json").exists()
    assert (tmp_path / "results.txt").exists()

def test_scan_respects_file_filter(tmp_path):
    """Scan respects file extension filtering."""
    config = Config.from_dict({
        "start_dirs": [str(SMALL_TEST_DATA_DIR)],
        "file_handlers": ["pan"],
        "file_extensions": [".csv"],  # Only CSV files
        "output_formats": ["json"],
        "output_dir": str(tmp_path)
    })
    
    run_main(config)
    
    # Verify only CSV-sourced results in output
    with open(tmp_path / "results.json") as f:
        data = json.load(f)
        for result in data["results"]:
            assert result["source_file"].endswith(".csv")
```

### 2. Timeout Enforcement

**File**: `tests/integration/test_timeout_enforcement.py`

**Tests**:
```python
def test_timeout_on_base64_xml(tmp_path):
    """Email handler times out on base64-xml-test.xml."""
    config = Config.from_dict({
        "start_dirs": [str(TEST_DATA_DIR / "pan")],
        "file_handlers": ["email"],
        "datahandler_timeout_seconds": 30,
        "output_formats": ["csv"],
        "output_dir": str(tmp_path)
    })
    
    start = time.time()
    exit_code = run_main(config)
    elapsed = time.time() - start
    
    # Should complete in ~30-45 seconds (30s timeout + overhead), not 2-4 minutes
    assert elapsed < 60
    assert exit_code == 0
    
    # Verify log contains timeout warning
    with open(LOG_FILE) as f:
        log_content = f.read()
        assert "TIMED OUT" in log_content or "timeout" in log_content.lower()

def test_partial_results_on_timeout(tmp_path):
    """Results are still collected even if handler times out."""
    # Create test file with both email-like strings and base64 data
    test_file = SMALL_TEST_DATA_DIR / "mixed_content.txt"
    test_file.write_text("user@example.com\n" + "a" * 1000 + "X" + "\n" + "admin@test.net")
    
    config = Config.from_dict({
        "start_dirs": [str(test_file.parent)],
        "file_handlers": ["email"],
        "datahandler_timeout_seconds": 5,
        "output_formats": ["json"],
        "output_dir": str(tmp_path)
    })
    
    run_main(config)
    
    # Even with timeout, should capture at least some results (before timeout)
    with open(tmp_path / "results.json") as f:
        data = json.load(f)
        results = data["results"]
        # Should have at least one email match (from unambiguous parts)
        assert any("user@example.com" in str(r) or "admin@test.net" in str(r) 
                  for r in results)
```

### 3. Graceful Shutdown

**File**: `tests/integration/test_graceful_shutdown.py`

**Tests**:
```python
def test_shutdown_on_ctrl_c(tmp_path):
    """Scan shuts down gracefully on Ctrl+C (SIGINT)."""
    config = Config.from_dict({
        "start_dirs": [str(LARGE_TEST_DATA_DIR)],  # Large enough to take time
        "file_handlers": ["pan", "email"],
        "output_formats": ["csv"],
        "output_dir": str(tmp_path)
    })
    
    # Start scan in background
    proc = mp.Process(target=run_main, args=(config,))
    proc.start()
    
    time.sleep(2)  # Let it start
    proc.terminate()  # Send SIGTERM
    proc.join(timeout=5)
    
    assert not proc.is_alive()  # Process exited cleanly
    
    # Verify output file was created (partial results OK)
    assert (tmp_path / "results.csv").exists()
    
    # Verify no leftover temp files
    temp_dir = Path("/tmp/piidigger_*")
    temp_files = list(Path("/tmp").glob("piidigger_*"))
    assert len(temp_files) == 0

def test_shutdown_cleanup(tmp_path):
    """All resources cleaned up on shutdown."""
    # Before scan: no locks
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    
    config = Config.from_dict({
        "start_dirs": [str(SMALL_TEST_DATA_DIR)],
        "file_handlers": ["pan"],
        "output_formats": ["csv"],
        "output_dir": str(tmp_path)
    })
    
    run_main(config)
    
    # After scan: all locks released, temp files cleaned
    lock_files = list(lock_dir.glob("*"))
    assert len(lock_files) == 0
```

### 4. Edge Cases

**File**: `tests/integration/test_edge_cases.py`

**Tests**:
```python
def test_empty_directory_scan():
    """Scan completes successfully on empty directory."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    
    config = Config.from_dict({
        "start_dirs": [str(empty_dir)],
        "file_handlers": ["pan"],
        "output_formats": ["csv"]
    })
    
    exit_code = run_main(config)
    assert exit_code == 0

def test_directory_with_no_matches():
    """Scan completes on files with no PII."""
    config = Config.from_dict({
        "start_dirs": [str(TEST_DATA_DIR / "plaintext")],
        "file_handlers": ["pan"],
        "output_formats": ["json"]
    })
    
    run_main(config)
    # May have 0 results, but shouldn't error

def test_file_access_denied():
    """Scan handles permission denied errors gracefully."""
    restricted_dir = tmp_path / "restricted"
    restricted_dir.mkdir()
    restricted_file = restricted_dir / "forbidden.txt"
    restricted_file.write_text("secret data")
    os.chmod(restricted_file, 0o000)  # No permissions
    
    config = Config.from_dict({
        "start_dirs": [str(restricted_dir)],
        "file_handlers": ["pan"]
    })
    
    try:
        exit_code = run_main(config)
        # Should handle gracefully, not crash
        assert exit_code in (0, 1)  # Success or caught error
    finally:
        os.chmod(restricted_file, 0o644)  # Cleanup

def test_many_matches_single_file():
    """Scan handles file with 1000+ matches."""
    test_file = tmp_path / "many_matches.txt"
    # Write file with 1000 PAN-like strings
    lines = ["4111111111111111\n"] * 1000
    test_file.write_text("".join(lines))
    
    config = Config.from_dict({
        "start_dirs": [str(test_file.parent)],
        "file_handlers": ["pan"]
    })
    
    run_main(config)
    # Should complete without memory exhaustion
```

---

## E2E Tests

### 1. Baseline Comparison

**File**: `tests/e2e/test_baseline_comparison.py`

**Purpose**: Ensure refactored version produces identical output to original implementation.

**Tests**:
```python
def test_output_csv_matches_baseline(tmp_path):
    """CSV output matches baseline results."""
    config = Config.from_dict(BASELINE_CONFIG)
    config.output_dir = str(tmp_path)
    
    run_main(config)
    
    # Load generated output
    generated_path = tmp_path / "results.csv"
    with open(generated_path) as f:
        generated_csv = f.read()
    
    # Load baseline
    with open(BASELINE_CSV_PATH) as f:
        baseline_csv = f.read()
    
    # Compare
    assert generated_csv == baseline_csv

def test_output_json_matches_baseline(tmp_path):
    """JSON output has same results as baseline (order-independent)."""
    config = Config.from_dict(BASELINE_CONFIG)
    config.output_dir = str(tmp_path)
    
    run_main(config)
    
    # Load generated
    with open(tmp_path / "results.json") as f:
        generated = json.load(f)
    
    # Load baseline
    with open(BASELINE_JSON_PATH) as f:
        baseline = json.load(f)
    
    # Compare (sort results for order-independent comparison)
    gen_results = sorted(generated["results"], key=str)
    base_results = sorted(baseline["results"], key=str)
    assert gen_results == base_results

def test_match_count_matches_baseline():
    """Total match count equals baseline."""
    config = Config.from_dict(BASELINE_CONFIG)
    results = run_main_collect_results(config)
    
    baseline_count = sum(1 for _ in load_baseline_results())
    assert len(results) == baseline_count
```

---

## Test Fixtures

**File**: `tests/conftest.py`

```python
import pytest
from pathlib import Path
from piidigger.classes import Config

# Test data directories
TEST_DATA_DIR = Path(__file__).parent.parent / "testdata"
SMALL_TEST_DATA_DIR = Path(__file__).parent / "fixtures/sample_data"
LARGE_TEST_DATA_DIR = TEST_DATA_DIR

# Sample configurations
SAMPLE_CONFIG = {
    "start_dirs": [str(SMALL_TEST_DATA_DIR)],
    "file_handlers": ["pan", "email"],
    "file_extensions": [],  # All
    "mime_types": [],  # All
    "output_formats": ["csv"],
    "datahandler_timeout_seconds": 30,
}

BASELINE_CONFIG = {
    "start_dirs": [str(TEST_DATA_DIR)],
    "file_handlers": ["pan", "email", "ssn"],
    "output_formats": ["csv", "json"],
}

@pytest.fixture
def sample_config():
    """Provide sample configuration for tests."""
    return Config.from_dict(SAMPLE_CONFIG)

@pytest.fixture
def temp_output_dir(tmp_path):
    """Provide temporary output directory."""
    return tmp_path

@pytest.fixture
def sample_task():
    """Provide sample task for testing."""
    from piidigger.orchestration.tasks import Task
    return Task(
        task_id="test-task-1",
        task_type="scan_file",
        payload={"file_path": str(SMALL_TEST_DATA_DIR / "test.txt")},
        timeout_seconds=30
    )
```

---

## Running Tests

### All Tests
```bash
pytest tests/ -v
```

### With Coverage
```bash
pytest --cov=src/piidigger tests/ --cov-report=html
```

### Specific Test Categories
```bash
# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# E2E tests only
pytest tests/e2e/ -v

# Specific test file
pytest tests/unit/orchestration/test_handlers.py -v

# Specific test function
pytest tests/unit/orchestration/test_handlers.py::test_handle_scan_file -v
```

### Watch Mode (during development)
```bash
pytest-watch tests/ -- -v
```

---

## Coverage Requirements

**Target**: ≥ 80% coverage

**Breakdown**:
- `src/piidigger/orchestration/`: 95% (critical new code)
- `src/piidigger/classes.py`: 85% (modified)
- `src/piidigger/piidigger.py`: 80% (main function significantly changed)
- `src/piidigger/datahandlers/`: 75% (preserved, not modified)
- `src/piidigger/filehandlers/`: 75% (preserved, not modified)

**Exclude from coverage**:
- `__main__.py` (CLI entry point, difficult to test)
- Test files themselves
- Old ProcessManager class (if not removed, mark as deprecated)

---

## Known Test Challenges

### 1. Multiprocessing Tests Are Hard

**Challenge**: Multiprocessing code difficult to unit test (processes isolated, hard to mock).

**Solution**: 
- Test at integration level (actual processes)
- Use queue fixtures that can be inspected
- Test handler functions in isolation as unit tests

### 2. Timeout Tests Require Real Delays

**Challenge**: Timeout tests inherently slow (must wait for timeout to occur).

**Solution**:
- Use short timeouts (1-5 seconds) for tests
- Run timeout tests in separate test suite
- Use `-m slow` marker for slow tests

```bash
# Run only fast tests
pytest tests/ -m "not slow" -v

# Run only slow tests
pytest tests/ -m "slow" -v
```

### 3. File System Tests Need Cleanup

**Challenge**: Tests create files/directories, must clean up properly.

**Solution**: Use `pytest.tmp_path` fixture (automatic cleanup).

---

## Success Criteria for Testing

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] All E2E tests pass
- [ ] Coverage ≥ 80%
- [ ] base64-xml-test.xml completes in <5 minutes
- [ ] Output matches baseline comparison
- [ ] No test flakiness (tests pass consistently)
- [ ] Linting passes: `ruff check tests/`
