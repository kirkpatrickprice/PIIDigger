# Testing Strategy for Architecture Refactor

**Branch**: `refactor`
**Status**: Historical reference — describes the test strategy used to build the now-complete 2.0 architecture. For current, project-wide testing standards see [docs/architecture/quality/testing-requirements.md](../architecture/quality/testing-requirements.md).
**Last Updated**: 2026-07-06
**Reference**: [ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md), [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md)

---

## Philosophy

- **Unit tests**: each component in strict isolation — no process tree, no filesystem, no queues unless the component is literally a queue handler.
- **Integration tests**: components wired together with real processes and real files, but scoped to a single phase's deliverables.
- **E2E tests**: full `run_scan()` on real test data; output compared to baseline.
- **Business logic is unit-testable without orchestration** — that is a hard design requirement, not a wish. If a data handler, file handler, or output sink cannot be tested without spinning up `mp.Process`, something is wrong with its contract.

---

## Test Structure

```
tests/
├── conftest.py                     # Top-level fixtures: tmp dirs, config factories
├── unit/
│   ├── conftest.py
│   ├── models/
│   │   ├── test_tasks.py           # Task, TaskResult, TaskType validation
│   │   ├── test_config.py          # Config.from_toml, Config.default, validation errors
│   │   └── test_results.py         # ResultRecord, lineage fields
│   ├── orchestration/
│   │   ├── test_worker.py          # worker_loop, DISPATCH, temp workspace cleanup
│   │   ├── test_coordinator.py     # pending counter, fan-out, deadline detection
│   │   ├── test_logging_setup.py   # QueueHandler, QueueListener lifecycle
│   │   └── test_progress.py        # ProgressDisplay: TTY and non-TTY modes
│   ├── datahandlers/
│   │   ├── test_pan.py
│   │   ├── test_email.py
│   │   ├── test_phonenum.py
│   │   └── test_trackdata.py
│   ├── filehandlers/
│   │   ├── test_plaintext.py
│   │   ├── test_pdf.py
│   │   ├── test_docx.py
│   │   ├── test_xlsx.py
│   │   └── test_xls.py
│   └── outputhandlers/
│       ├── test_csv_sink.py
│       ├── test_json_sink.py
│       └── test_text_sink.py
├── integration/
│   ├── conftest.py
│   ├── test_worker_pool.py         # Pool lifecycle: start, dispatch, collect, shutdown
│   ├── test_coordinator_loop.py    # Coordinator fan-out on synthetic + real task chains
│   ├── test_full_scan.py           # run_scan() on small test directories
│   ├── test_timeout_enforcement.py # Deadline detection terminates hung workers
│   ├── test_graceful_shutdown.py   # Ctrl+C exits cleanly
│   └── test_output_formats.py      # All three output formats correct
├── e2e/
│   ├── conftest.py
│   ├── test_baseline_comparison.py # 2.0 output == 1.x baseline
│   └── test_full_scan_testdata.py  # Full testdata/ directory scan
└── fixtures/
    ├── sample_data/                # Small synthetic test files
    ├── baseline_results/           # 1.x reference output (generated once)
    └── zip/                        # ZIP test fixtures (Phase 5)
```

---

## Fixtures — `tests/conftest.py`

All tests share a `Config` construction pattern. Since `Config` is a validated Pydantic model, tests use either `Config.default()` with `model_copy(update={...})` for simple overrides, or write a minimal TOML to `tmp_path` for full-load testing.

```python
import queue
from pathlib import Path

import pytest
from click.testing import CliRunner

from piidigger.models.config import Config
from piidigger.orchestration.context import WorkerContext

TEST_DATA_DIR = Path(__file__).parent.parent / "testdata"
SMALL_TEST_DATA_DIR = Path(__file__).parent / "fixtures" / "sample_data"


@pytest.fixture
def default_config():
    """Minimal default Config for unit tests."""
    return Config.default()


@pytest.fixture
def scan_config(tmp_path):
    """Config targeting the small fixture dataset, output to tmp_path."""
    return Config.default().model_copy(update={
        "start_dirs": [SMALL_TEST_DATA_DIR],
        "log_file": tmp_path / "test.log",
        "results": {"csv": tmp_path / "results.csv"},
    })


@pytest.fixture
def fake_context(tmp_path):
    """WorkerContext with in-process queues — no mp.Process needed for unit tests."""
    return WorkerContext(
        config=Config.default(),
        task_queue=queue.SimpleQueue(),
        result_queue=queue.SimpleQueue(),
        log_queue=queue.SimpleQueue(),
        stop_event=__import__("threading").Event(),
    )


@pytest.fixture
def cli_runner():
    return CliRunner()
```

> **Note on `fake_context`:** handler unit tests use `queue.SimpleQueue` and `threading.Event` instead of their `mp.*` equivalents. This keeps tests in-process and avoids spawn overhead. The real `WorkerContext` uses `mp.Queue`/`mp.Event` — the integration tests verify the real thing.

---

## Unit Tests

### Task Model — `tests/unit/models/test_tasks.py`

```python
import pickle
from pydantic import ValidationError
import pytest
from piidigger.models.tasks import Task, TaskResult, TaskType, SHUTDOWN

def test_task_creation_valid():
    t = Task(task_type=TaskType.SCAN_FILE, payload={"file_path": "/tmp/x.txt"})
    assert t.task_type == TaskType.SCAN_FILE
    assert t.task_id  # auto-generated

def test_task_is_frozen():
    t = Task(task_type=TaskType.ENUM_DIR, payload={})
    with pytest.raises(Exception):  # ValidationError or FrozenInstanceError
        t.task_id = "mutated"

def test_task_rejects_invalid_timeout():
    with pytest.raises(ValidationError):
        Task(task_type=TaskType.ENUM_DIR, payload={}, timeout_seconds=0)

def test_task_rejects_unknown_task_type():
    with pytest.raises(ValidationError):
        Task(task_type="not_a_type", payload={})

def test_task_pickles_cleanly():
    t = Task(task_type=TaskType.SCAN_FILE, payload={"x": 1})
    assert pickle.loads(pickle.dumps(t)) == t

def test_task_result_rejects_invalid_status():
    with pytest.raises(ValidationError):
        TaskResult(task_id="x", task_type=TaskType.SCAN_FILE, status="pending", duration_seconds=0)

def test_shutdown_sentinel_is_not_a_task():
    assert not isinstance(SHUTDOWN, Task)
```

---

### Config Model — `tests/unit/models/test_config.py`

```python
import pytest
from pathlib import Path
from pydantic import ValidationError
from piidigger.models.config import Config

def test_default_config_loads():
    c = Config.default()
    assert c.max_workers >= 1
    assert c.default_timeout_seconds > 0

def test_from_toml_valid(tmp_path):
    config_file = tmp_path / "test.toml"
    config_file.write_text('[scan]\nstart_dirs = ["/tmp"]\n')
    c = Config.from_toml(config_file)
    assert c.start_dirs == [Path("/tmp")]

def test_from_toml_missing_file(tmp_path):
    """Missing file should raise with a clear message, not AttributeError."""
    with pytest.raises(FileNotFoundError, match="piidigger.toml"):
        Config.from_toml(tmp_path / "piidigger.toml")

def test_from_toml_invalid_toml(tmp_path):
    config_file = tmp_path / "bad.toml"
    config_file.write_text("this is not toml ][")
    with pytest.raises(Exception, match="Invalid"):
        Config.from_toml(config_file)

def test_from_toml_nonexistent_start_dir(tmp_path):
    """start_dirs that don't exist should fail validation with a clear message."""
    config_file = tmp_path / "test.toml"
    config_file.write_text('[scan]\nstart_dirs = ["/no/such/path/ever"]\n')
    with pytest.raises((ValidationError, SystemExit)):
        Config.from_toml(config_file)

def test_model_copy_override(default_config, tmp_path):
    updated = default_config.model_copy(update={"start_dirs": [tmp_path]})
    assert updated.start_dirs == [tmp_path]
```

---

### WorkerContext — `tests/unit/orchestration/test_worker.py`

```python
import queue, threading
from piidigger.models.tasks import Task, TaskType, TaskStarted, TaskResult, SHUTDOWN
from piidigger.orchestration.worker import worker_loop, DISPATCH

def test_dispatch_table_contains_core_types():
    assert TaskType.ENUM_DIR in DISPATCH
    assert TaskType.SCAN_FILE in DISPATCH

def test_noop_handler_returns_ok(fake_context):
    from piidigger.models.tasks import TaskType
    task = Task(task_type=TaskType.NOOP, payload={})
    result = DISPATCH[TaskType.NOOP](task, fake_context, None)
    assert result.status == "ok"

def test_worker_loop_processes_and_shuts_down(fake_context):
    import threading
    task = Task(task_type=TaskType.NOOP, payload={})
    fake_context.task_queue.put(task)
    fake_context.task_queue.put(SHUTDOWN)

    t = threading.Thread(target=worker_loop, args=(fake_context,))
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()

    # One TaskStarted + one TaskResult in result_queue
    msgs = []
    while not fake_context.result_queue.empty():
        msgs.append(fake_context.result_queue.get_nowait())
    assert any(isinstance(m, TaskStarted) for m in msgs)
    assert any(isinstance(m, TaskResult) and m.status == "ok" for m in msgs)

def test_dispatch_exception_becomes_error_result(fake_context):
    """Handler exceptions must not propagate — they become status='error' results."""
    def bad_handler(task, ctx, logger):
        raise RuntimeError("boom")

    from piidigger.orchestration.worker import _dispatch
    task = Task(task_type=TaskType.NOOP, payload={})
    result = _dispatch(task, fake_context, None, handler_override=bad_handler)
    assert result.status == "error"
    assert "boom" in result.error_message
```

---

### Coordinator — `tests/unit/orchestration/test_coordinator.py`

```python
def test_pending_arithmetic_stays_consistent():
    """pending -= 1 then += N for new_tasks is the core invariant."""
    pending = 3
    result_new_tasks = [{"task_type": "enum_dir", "payload": {}} for _ in range(5)]
    pending -= 1
    pending += len(result_new_tasks)
    assert pending == 7  # 3 - 1 + 5

def test_deadline_detection_synthesizes_timeout_result(fake_context):
    from piidigger.orchestration.coordinator import _check_worker_deadlines
    import time
    in_flight = {
        "task-abc": {"pid": 99999, "start_time": time.monotonic() - 120,
                     "timeout_seconds": 30}
    }
    synthesized = _check_worker_deadlines(in_flight, workers={})
    assert len(synthesized) == 1
    assert synthesized[0].status == "timeout"
    assert synthesized[0].task_id == "task-abc"
```

---

### Logging Setup — `tests/unit/orchestration/test_logging_setup.py`

```python
import logging, queue as q
from piidigger.orchestration.logging_setup import build_worker_logger, start_listener, stop_listener

def test_build_worker_logger_puts_records_on_queue():
    log_queue = q.SimpleQueue()
    logger = build_worker_logger(log_queue, name="test")
    logger.warning("hello from test")
    record = log_queue.get_nowait()
    assert record.getMessage() == "hello from test"

def test_listener_writes_to_file(tmp_path):
    log_file = tmp_path / "test.log"
    log_queue = q.Queue()
    listener = start_listener(log_queue, log_file=str(log_file), log_level="DEBUG")
    logger = build_worker_logger(log_queue, name="integration")
    logger.info("written via listener")
    stop_listener(listener)
    assert "written via listener" in log_file.read_text()
```

---

### Progress Display — `tests/unit/orchestration/test_progress.py`

```python
from piidigger.orchestration.progress import ProgressDisplay

def test_progress_display_no_tty_is_silent(capsys):
    """In non-TTY mode, update() and log_event() produce no output."""
    display = ProgressDisplay(is_tty=False)
    display.start()
    display.update({"files_scanned": 5, "bytes_scanned": 1024})
    display.log_event("warning", "test warning")
    display.stop()
    captured = capsys.readouterr()
    assert "test warning" not in captured.err  # progress events are silent

def test_progress_display_counters_accumulate():
    display = ProgressDisplay(is_tty=False)
    display.start()
    display.update({"files_scanned": 3})
    display.update({"files_scanned": 2, "bytes_scanned": 512})
    assert display.totals["files_scanned"] == 5
    assert display.totals["bytes_scanned"] == 512
    display.stop()
```

---

### Data Handlers

Each handler test follows the same pattern: call `find_matches(text)`, assert the result dict.

```python
# tests/unit/datahandlers/test_pan.py
from piidigger.datahandlers.pan import PanHandler

def test_finds_valid_pan():
    h = PanHandler()
    result = h.find_matches("Card: 4111111111111111")
    assert "4111111111111111" in result.get("pan", set())

def test_does_not_match_invalid_luhn():
    h = PanHandler()
    result = h.find_matches("Card: 4111111111111112")
    assert not result.get("pan")

def test_returns_correct_type():
    h = PanHandler()
    result = h.find_matches("no card here")
    assert isinstance(result, dict)
    assert all(isinstance(v, set) for v in result.values())
```

---

### File Handlers

Each handler test: construct a `FilesystemItem` from a real fixture file, call `read()`, assert text chunks are non-empty strings.

```python
# tests/unit/filehandlers/test_plaintext.py
from pathlib import Path
from piidigger.filehandlers.plaintext import PlaintextHandler
from piidigger.orchestration.sources import FilesystemItem

FIXTURE = Path(__file__).parent.parent.parent / "testdata" / "plaintext" / "test.txt"

def test_read_yields_strings():
    item = FilesystemItem(path=FIXTURE)
    handler = PlaintextHandler()
    chunks = list(handler.read(item))
    assert chunks
    assert all(isinstance(c, str) for c in chunks)

def test_read_nonempty_content():
    item = FilesystemItem(path=FIXTURE)
    combined = "".join(PlaintextHandler().read(item))
    assert len(combined) > 0
```

---

### Output Sinks

```python
# tests/unit/outputhandlers/test_csv_sink.py
import csv
from piidigger.outputhandlers.csv import CsvSink
from piidigger.models.results import ResultRecord

def test_csv_sink_writes_header_and_row(tmp_path):
    output_file = tmp_path / "results.csv"
    sink = CsvSink(output_file)
    sink.open()
    sink.write(ResultRecord(
        source_path="/data/test.txt",
        handler="pan",
        matches={"pan": ["4111111111111111"]},
    ))
    sink.close()

    rows = list(csv.DictReader(output_file.open()))
    assert len(rows) == 1
    assert rows[0]["source_path"] == "/data/test.txt"
    assert rows[0]["handler"] == "pan"

def test_csv_sink_lineage_fields_present_for_on_disk(tmp_path):
    output_file = tmp_path / "results.csv"
    sink = CsvSink(output_file)
    sink.open()
    sink.write(ResultRecord(source_path="/x.txt", handler="email",
                            matches={"email": ["a@b.com"]}))
    sink.close()
    rows = list(csv.DictReader(output_file.open()))
    # Lineage columns exist even for on-disk files; values are empty/null
    assert "source_member_path" in rows[0]
    assert rows[0]["source_member_path"] == ""
```

---

## Integration Tests

### Worker Pool Lifecycle — `tests/integration/test_worker_pool.py`

```python
import multiprocessing as mp
from piidigger.models.tasks import Task, TaskType, TaskResult, SHUTDOWN
from piidigger.orchestration.worker import start_worker_pool, broadcast_shutdown, join_workers
from piidigger.orchestration.context import WorkerContext
from piidigger.models.config import Config

def test_pool_dispatches_and_collects(tmp_path):
    task_q = mp.Queue()
    result_q = mp.Queue()
    log_q = mp.Queue()
    ctx = WorkerContext(
        config=Config.default(),
        task_queue=task_q,
        result_queue=result_q,
        log_queue=log_q,
        stop_event=mp.Event(),
    )
    workers = start_worker_pool(ctx, n_workers=2)

    for _ in range(6):
        task_q.put(Task(task_type=TaskType.NOOP, payload={}))
    broadcast_shutdown(task_q, n_workers=2)
    join_workers(workers, timeout=10)

    results = []
    while not result_q.empty():
        msg = result_q.get_nowait()
        if isinstance(msg, TaskResult):
            results.append(msg)

    assert len(results) == 6
    assert all(r.status == "ok" for r in results)
```

---

### Coordinator Fan-out — `tests/integration/test_coordinator_loop.py`

```python
def test_coordinator_reaches_zero_on_synthetic_tree(tmp_path):
    """Fan-out loop terminates correctly when all tasks are accounted for.
    Uses stub handlers that return synthetic new_tasks without touching the filesystem."""
    ...  # Build ctx with stub DISPATCH, run coordinator, assert clean exit

def test_ctrl_c_exits_within_timeout(tmp_path):
    """KeyboardInterrupt during coordinator loop exits cleanly."""
    import signal, subprocess, sys, time
    proc = subprocess.Popen([sys.executable, "-m", "piidigger", "scan",
                             "--conf-file", str(tmp_path / "empty.toml")])
    time.sleep(1)
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=10)
    assert proc.returncode in (0, 1, 130)  # clean exit codes
```

---

### Full Scan — `tests/integration/test_full_scan.py`

```python
from piidigger.run import run_scan
from piidigger.models.config import Config

def test_scan_produces_csv_output(scan_config):
    exit_code = run_scan(scan_config)
    assert exit_code == 0
    csv_path = scan_config.results["csv"]
    assert csv_path.exists()
    assert csv_path.stat().st_size > 0

def test_scan_all_output_formats(tmp_path):
    config = Config.default().model_copy(update={
        "start_dirs": [SMALL_TEST_DATA_DIR],
        "results": {
            "csv": tmp_path / "results.csv",
            "json": tmp_path / "results.json",
            "text": tmp_path / "results.txt",
        },
    })
    assert run_scan(config) == 0
    assert (tmp_path / "results.csv").exists()
    assert (tmp_path / "results.json").exists()
    assert (tmp_path / "results.txt").exists()

def test_scan_empty_directory(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    config = Config.default().model_copy(update={"start_dirs": [empty]})
    assert run_scan(config) == 0

def test_scan_permission_denied_file(tmp_path):
    """A file we can't read should be logged and skipped; scan should not crash."""
    import os
    restricted = tmp_path / "forbidden.txt"
    restricted.write_text("secret")
    os.chmod(restricted, 0o000)
    config = Config.default().model_copy(update={"start_dirs": [tmp_path]})
    try:
        exit_code = run_scan(config)
        assert exit_code in (0, 1)
    finally:
        os.chmod(restricted, 0o644)
```

---

### Timeout Enforcement — `tests/integration/test_timeout_enforcement.py`

```python
import time
from piidigger.run import run_scan
from piidigger.models.config import Config

@pytest.mark.slow
def test_base64_xml_completes_under_five_minutes(tmp_path):
    config = Config.default().model_copy(update={
        "start_dirs": [TEST_DATA_DIR / "pan"],
        "data_handlers": ["email"],
        "default_timeout_seconds": 30,
        "results": {"csv": tmp_path / "results.csv"},
        "log_file": tmp_path / "test.log",
    })
    start = time.monotonic()
    run_scan(config)
    elapsed = time.monotonic() - start
    assert elapsed < 300  # < 5 minutes

    log_text = (tmp_path / "test.log").read_text()
    assert "timeout" in log_text.lower()
```

---

### CLI Layer — `tests/integration/test_cli.py`

```python
from click.testing import CliRunner
from piidigger.cli.main import cli

def test_cli_scan_exits_zero(tmp_path, scan_config_toml):
    runner = CliRunner()
    result = runner.invoke(cli, ["scan", "--conf-file", str(scan_config_toml)])
    assert result.exit_code == 0

def test_cli_config_generate(tmp_path):
    runner = CliRunner()
    out = tmp_path / "generated.toml"
    result = runner.invoke(cli, ["config", "generate", "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()

def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert "2.0.0" in result.output
```

---

## E2E Tests

### Baseline Comparison — `tests/e2e/test_baseline_comparison.py`

```python
import json, csv
from pathlib import Path
from piidigger.run import run_scan
from piidigger.models.config import Config

BASELINE_DIR = Path(__file__).parent.parent / "fixtures" / "baseline_results"

@pytest.mark.e2e
def test_csv_matches_baseline(tmp_path):
    config = Config.default().model_copy(update={
        "start_dirs": [TEST_DATA_DIR],
        "results": {"csv": tmp_path / "results.csv"},
    })
    run_scan(config)
    generated = sorted(csv.DictReader(open(tmp_path / "results.csv")), key=str)
    baseline  = sorted(csv.DictReader(open(BASELINE_DIR / "baseline.csv")), key=str)
    assert generated == baseline

@pytest.mark.e2e
def test_json_matches_baseline(tmp_path):
    config = Config.default().model_copy(update={
        "start_dirs": [TEST_DATA_DIR],
        "results": {"json": tmp_path / "results.json"},
    })
    run_scan(config)
    generated = sorted(json.loads(l) for l in open(tmp_path / "results.json"))
    baseline  = sorted(json.loads(l) for l in open(BASELINE_DIR / "baseline.json"))
    assert generated == baseline
```

---

## Running Tests

```bash
# All tests
uv run pytest tests/ -v

# Fast tests only (skip slow timeout tests and e2e)
uv run pytest tests/ -m "not slow and not e2e" -v

# With coverage report
uv run pytest tests/ --cov=src/piidigger --cov-report=term-missing

# Specific phase work
uv run pytest tests/unit/models/ -v                    # Phase 1
uv run pytest tests/unit/orchestration/ -v             # Phase 1-2
uv run pytest tests/integration/test_worker_pool.py -v # Phase 1 integration
uv run pytest tests/integration/ -v                    # Phase 3+
uv run pytest tests/e2e/ -m e2e -v                    # Phase 4+
```

### pytest markers

Declare in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: tests that take > 5 seconds (timeout enforcement, large scans)",
    "e2e: end-to-end tests requiring full testdata/ and baseline files",
]
```

---

## Coverage Requirements

| Module | Target | Notes |
|---|---|---|
| `src/piidigger/orchestration/` | ≥ 90% | Critical new code |
| `src/piidigger/models/` | ≥ 90% | Validation logic |
| `src/piidigger/datahandlers/` | ≥ 80% | Existing logic, verified correct |
| `src/piidigger/filehandlers/` | ≥ 80% | Existing logic |
| `src/piidigger/outputhandlers/` | ≥ 85% | New OutputSink contract |
| `src/piidigger/run.py` | ≥ 75% | Thin wiring layer |
| `src/piidigger/cli/` | — | Tested via `CliRunner`; exclude from cov target |

**Minimum overall**: ≥ 80%

---

## Known Test Challenges

### Multiprocessing tests on Windows `spawn`

Integration tests that spin up real `mp.Process` workers are slower on Windows due to `spawn` reimporting the entire module tree per process. Keep the unit tests using `threading.Thread` + in-process queues (`queue.SimpleQueue`, `threading.Event`) for speed. Reserve real `mp.Process` for integration tests that are explicitly verifying cross-process behavior.

### Slow timeout tests

Timeout tests inherently take seconds. Mark them `@pytest.mark.slow` and exclude from the default `pytest` run. A CI job can include them on schedule or on release branches.

### `rich.Live` in test environments

`rich.Live` detects whether it has a TTY. In `pytest` (piped output), `Console.is_terminal` is `False` and `ProgressDisplay` goes into no-op mode — this is correct behavior. If a test needs to assert on progress output, pass `is_tty=True` explicitly and use `rich`'s test utilities or capture `stderr`.

### E2E baseline generation

The baseline in `tests/fixtures/baseline_results/` must be generated from the 1.x `main` branch **before** the old orchestration code is deleted in Phase 4. Procedure:

1. Check out `main` branch
2. Run `piidigger` against `testdata/` with all output formats
3. Copy output files to `tests/fixtures/baseline_results/`
4. Commit to `refactor` branch before Phase 4 begins

Any intentional format difference in 2.0 (e.g. lineage columns present but empty) must be documented and the comparison adjusted accordingly.
