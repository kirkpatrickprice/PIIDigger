"""Integration tests for the Phase 1 worker pool.

All process targets used here are module-level functions so that
Windows multiprocessing spawn can import them without re-running test code.
"""

from __future__ import annotations

import logging
import logging.handlers
import multiprocessing as mp
import pickle
import queue
import threading
import time
from pathlib import Path

import pytest

from piidigger.models.config import Config
from piidigger.models.tasks import SHUTDOWN, Task, TaskResult, TaskStarted, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger, start_listener, stop_listener
from piidigger.orchestration.worker import broadcast_shutdown, join_workers, start_worker_pool, worker_loop
from piidigger.orchestration.worker._loop import DISPATCH, _dispatch, _handle_noop

# ---------------------------------------------------------------------------
# Logging unit test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_worker_logger_sends_to_queue() -> None:
    """build_worker_logger() returns a logger that queues log records."""
    log_queue: mp.Queue[object] = mp.Queue()
    logger = build_worker_logger(log_queue, name="test-logger")
    logger.warning("hello from test")

    # The record should be immediately available (same process, no spawn)
    record = log_queue.get(timeout=1)
    assert isinstance(record, logging.LogRecord)
    assert "hello from test" in record.getMessage()


@pytest.mark.unit
def test_build_worker_logger_idempotent() -> None:
    """Calling build_worker_logger twice with the same name does not add handlers."""
    log_queue: mp.Queue[object] = mp.Queue()
    logger1 = build_worker_logger(log_queue, name="idempotent-test")
    logger2 = build_worker_logger(log_queue, name="idempotent-test")
    assert logger1 is logger2
    queue_handlers = [h for h in logger1.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(queue_handlers) == 1


# ---------------------------------------------------------------------------
# WorkerContext pickling across spawn
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_worker_context_config_is_picklable() -> None:
    """Config (the non-OS payload inside WorkerContext) must be picklable.

    mp.Queue and mp.Event are OS-level proxy objects that reject direct
    pickle.dumps — they are shared via the multiprocessing spawn inheritance
    path, not raw pickle.  test_noop_pool_dispatches_and_collects is the live
    proof that the full WorkerContext crosses the spawn boundary correctly.
    """
    config = Config()
    restored: Config = pickle.loads(pickle.dumps(config))
    assert type(restored) is Config


# ---------------------------------------------------------------------------
# NOOP pool integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_noop_pool_dispatches_and_collects() -> None:
    """Start 2 workers, dispatch 10 NOOP tasks, collect all 10 results, shut down."""
    n_workers = 2
    n_tasks = 10

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = WorkerContext(
        config=Config(),
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=stop_event,
    )

    tasks = [Task(task_type=TaskType.NOOP) for _ in range(n_tasks)]
    for t in tasks:
        task_queue.put(t)

    workers = start_worker_pool(ctx, n_workers)
    task_ids = {t.task_id for t in tasks}

    results: list[TaskResult] = []
    deadline = time.monotonic() + 30
    while len(results) < n_tasks and time.monotonic() < deadline:
        try:
            msg = result_queue.get(timeout=1)
        except queue.Empty:
            continue
        if isinstance(msg, TaskResult):
            results.append(msg)
        # TaskStarted heartbeats are silently consumed here

    broadcast_shutdown(task_queue, n_workers)
    join_workers(workers, timeout=10)

    assert len(results) == n_tasks
    assert {r.task_id for r in results} == task_ids
    assert all(r.status == "ok" for r in results)
    assert all(r.task_type is TaskType.NOOP for r in results)


# ---------------------------------------------------------------------------
# Worker log records reach the file
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_worker_logs_reach_file(tmp_path: Path) -> None:
    """Log records emitted inside a worker process appear in the log file."""
    log_file = tmp_path / "test_run.log"
    n_workers = 1

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = WorkerContext(
        config=Config(),
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=stop_event,
    )

    listener = start_listener(log_queue, log_file, "DEBUG")

    task_queue.put(Task(task_type=TaskType.NOOP))
    workers = start_worker_pool(ctx, n_workers)

    # Wait for the single result
    deadline = time.monotonic() + 15
    got_result = False
    while time.monotonic() < deadline:
        try:
            msg = result_queue.get(timeout=1)
            if isinstance(msg, TaskResult):
                got_result = True
                break
        except queue.Empty:
            pass

    broadcast_shutdown(task_queue, n_workers)
    join_workers(workers, timeout=10)
    stop_listener(listener)

    assert got_result, "never received TaskResult from worker"
    assert log_file.exists(), "log file was not created"
    content = log_file.read_text()
    assert "worker started" in content or "noop task" in content, (
        f"expected worker log records in file; got:\n{content}"
    )


# ---------------------------------------------------------------------------
# Thread-based worker_loop coverage
# (Runs worker_loop in a thread so pytest-cov can see its lines.
#  Subprocess-based tests exercise correctness; this exercises coverage.)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_worker_loop_in_thread_dispatches_task() -> None:
    """worker_loop() runs correctly in a thread; covers its body for pytest-cov."""
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = WorkerContext(
        config=Config(),
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=stop_event,
    )

    task_queue.put(Task(task_type=TaskType.NOOP))
    task_queue.put(SHUTDOWN)

    t = threading.Thread(target=worker_loop, args=(ctx,), daemon=True)
    t.start()
    t.join(timeout=10.0)
    assert not t.is_alive(), "worker_loop thread did not exit within 10 s"

    msgs = []
    while not result_queue.empty():
        msgs.append(result_queue.get_nowait())

    task_results = [m for m in msgs if isinstance(m, TaskResult)]
    heartbeats = [m for m in msgs if isinstance(m, TaskStarted)]
    assert len(task_results) == 1
    assert task_results[0].status == "ok"
    assert task_results[0].task_type is TaskType.NOOP
    assert len(heartbeats) == 1


# ---------------------------------------------------------------------------
# _dispatch unit tests (no subprocess needed)
# ---------------------------------------------------------------------------


def _make_minimal_ctx() -> WorkerContext:
    return WorkerContext(
        config=Config(),
        task_queue=mp.Queue(),
        result_queue=mp.Queue(),
        log_queue=mp.Queue(),
        stop_event=mp.Event(),
    )


@pytest.mark.unit
def test_dispatch_no_handler_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_dispatch returns status='error' when no handler is registered for the task type."""
    log_queue: mp.Queue[object] = mp.Queue()
    logger = build_worker_logger(log_queue, name="dispatch-test-no-handler")
    ctx = _make_minimal_ctx()
    task = Task(task_type=TaskType.NOOP)

    monkeypatch.delitem(DISPATCH, TaskType.NOOP)

    result = _dispatch(task, ctx, logger)
    assert result.status == "error"
    assert result.error_message is not None
    assert "no handler registered" in result.error_message


@pytest.mark.unit
def test_dispatch_handler_exception_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_dispatch wraps an unhandled handler exception into a status='error' result."""
    log_queue: mp.Queue[object] = mp.Queue()
    logger = build_worker_logger(log_queue, name="dispatch-test-exc")
    ctx = _make_minimal_ctx()
    task = Task(task_type=TaskType.NOOP)

    def _fail(_t: object, _c: object, _lg: object) -> None:
        raise RuntimeError("deliberate failure for test")

    monkeypatch.setitem(DISPATCH, TaskType.NOOP, _fail)  # type: ignore[arg-type]

    result = _dispatch(task, ctx, logger)
    assert result.status == "error"
    assert "deliberate failure" in (result.error_message or "")
    assert result.duration_seconds is not None


@pytest.mark.unit
def test_handle_noop_with_delay() -> None:
    """_handle_noop with delay_seconds > 0 sleeps and still returns ok."""
    log_queue: mp.Queue[object] = mp.Queue()
    logger = build_worker_logger(log_queue, name="noop-delay-test")
    ctx = _make_minimal_ctx()
    task = Task(task_type=TaskType.NOOP, payload={"delay_seconds": 0.01})

    result = _handle_noop(task, ctx, logger)
    assert result.status == "ok"
    assert result.task_type is TaskType.NOOP


# ---------------------------------------------------------------------------
# join_workers straggler path
# ---------------------------------------------------------------------------


def _sleepy_worker() -> None:
    """Target for a process that sleeps indefinitely; used to test straggler path."""
    import time

    time.sleep(60)


@pytest.mark.unit
def test_join_workers_terminates_straggler() -> None:
    """join_workers force-terminates a process that does not exit within timeout."""
    p = mp.Process(target=_sleepy_worker)
    p.start()
    assert p.is_alive()

    join_workers([p], timeout=0.1)

    assert not p.is_alive(), "straggler process was not terminated by join_workers"
    p.join()
