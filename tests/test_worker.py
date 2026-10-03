"""Integration tests for the Phase 1 worker pool.

All process targets used here are module-level functions so that
Windows multiprocessing spawn can import them without re-running test code.
"""

from __future__ import annotations

import logging
import logging.handlers
import multiprocessing as mp
import os
import pickle
import queue
import threading
import time
from pathlib import Path

import pytest

from piidigger.models.config import Config
from piidigger.models.tasks import SHUTDOWN, Task, TaskResult, TaskStarted, TaskType, WorkerReady
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger, start_listener, stop_listener
from piidigger.orchestration.pool import WorkerPool, spawn_worker
from piidigger.orchestration.worker import broadcast_shutdown, worker_loop
from piidigger.orchestration.worker._loop import DISPATCH, _dispatch, _handle_noop

_POOL_LOG = logging.getLogger("tests.worker.pool")


def _start_pool(ctx: WorkerContext, n_workers: int) -> WorkerPool:
    pool = WorkerPool(lambda: spawn_worker(ctx), logger=_POOL_LOG)
    pool.start(n_workers)
    return pool


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
def test_build_worker_logger_follows_a_new_queue() -> None:
    """A second call with a different queue moves the logger to that queue.

    Loggers are process-wide singletons.  The old check for "any QueueHandler"
    kept a logger bound to the first queue it saw, so a second run in the same
    process logged into a queue nobody read.
    """
    first: mp.Queue[object] = mp.Queue()
    second: mp.Queue[object] = mp.Queue()
    build_worker_logger(first, name="test-logger-rebind")
    logger = build_worker_logger(second, name="test-logger-rebind")

    logger.warning("after rebind")

    record = second.get(timeout=1)
    assert record.getMessage() == "after rebind"
    assert first.empty()
    handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(handlers) == 1


@pytest.mark.unit
def test_build_worker_logger_same_queue_adds_no_duplicate_handler() -> None:
    log_queue: mp.Queue[object] = mp.Queue()
    build_worker_logger(log_queue, name="test-logger-same")
    logger = build_worker_logger(log_queue, name="test-logger-same")

    handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(handlers) == 1


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

    pool = _start_pool(ctx, n_workers)
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
    pool.join(timeout=10)

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
    pool = _start_pool(ctx, n_workers)

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
    pool.join(timeout=10)
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
# WorkerPool.join straggler path, against a real process
# ---------------------------------------------------------------------------


def _sleepy_worker() -> None:
    """Target for a process that sleeps indefinitely; used to test straggler path."""
    import time

    time.sleep(60)


def _start_sleepy() -> mp.Process:
    proc = mp.Process(target=_sleepy_worker, daemon=True)
    proc.start()
    return proc


@pytest.mark.integration
def test_pool_join_stops_a_real_process_that_outlives_the_budget() -> None:
    """join() stops a real process that ignores shutdown, not just a fake one."""
    pool = WorkerPool(_start_sleepy, logger=_POOL_LOG)
    pool.start(1)
    (proc,) = pool.processes
    assert proc.is_alive()

    pool.join(timeout=0.1)

    assert not proc.is_alive(), "join() did not stop a process that outlived its budget"
    assert pool.stragglers == []


# ---------------------------------------------------------------------------
# WorkerReady check-in
# ---------------------------------------------------------------------------


def _thread_ctx() -> WorkerContext:
    return WorkerContext(
        config=Config(),
        task_queue=mp.Queue(),
        result_queue=mp.Queue(),
        log_queue=mp.Queue(),
        stop_event=mp.Event(),
    )


@pytest.mark.unit
def test_worker_checks_in_on_startup() -> None:
    """A worker's first message says it is up, which is what the lost-task sweep relies on."""
    ctx = _thread_ctx()
    worker = threading.Thread(target=worker_loop, args=(ctx,), daemon=True)
    worker.start()
    try:
        message = ctx.result_queue.get(timeout=10)
    finally:
        ctx.task_queue.put(SHUTDOWN)
        worker.join(timeout=10)

    assert isinstance(message, WorkerReady)
    assert message.worker_pid == os.getpid()


@pytest.mark.unit
def test_worker_checks_in_before_starting_a_waiting_task() -> None:
    """Even with work already queued, WorkerReady comes first, then TaskStarted."""
    ctx = _thread_ctx()
    task = Task(task_type=TaskType.NOOP)
    ctx.task_queue.put(task)
    worker = threading.Thread(target=worker_loop, args=(ctx,), daemon=True)
    worker.start()
    try:
        first = ctx.result_queue.get(timeout=10)
        second = ctx.result_queue.get(timeout=10)
    finally:
        ctx.task_queue.put(SHUTDOWN)
        worker.join(timeout=10)

    assert isinstance(first, WorkerReady)
    assert isinstance(second, TaskStarted)
    assert second.task_id == task.task_id


# ---------------------------------------------------------------------------
# stop_event and the bounded listener stop
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_worker_drops_a_task_taken_after_stop_is_requested() -> None:
    """Once teardown has begun, a queued task is leftover work: exit, do not run it."""
    ctx = _thread_ctx()
    worker = threading.Thread(target=worker_loop, args=(ctx,), daemon=True)
    worker.start()
    assert isinstance(ctx.result_queue.get(timeout=10), WorkerReady)  # now waiting in get()

    ctx.stop_event.set()
    ctx.task_queue.put(Task(task_type=TaskType.NOOP, payload={"delay_seconds": 30}))
    worker.join(timeout=10)

    assert not worker.is_alive()
    with pytest.raises(queue.Empty):
        ctx.result_queue.get(timeout=0.5)  # no TaskStarted: the task never ran


class _BlockingHandler(logging.Handler):
    """A handler that blocks until released, standing in for a listener stuck on a half-written record."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self.release = release

    def emit(self, record: logging.LogRecord) -> None:
        self.release.wait()


@pytest.mark.unit
def test_stop_listener_gives_up_instead_of_hanging() -> None:
    release = threading.Event()
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    listener = logging.handlers.QueueListener(log_queue, _BlockingHandler(release))
    listener.start()
    log_queue.put(logging.makeLogRecord({"msg": "stuck"}))
    try:
        started = time.monotonic()
        stopped = stop_listener(listener, timeout=0.2)
        elapsed = time.monotonic() - started

        assert stopped is False
        assert elapsed < 2.0, "stop_listener waited far longer than its timeout"
        assert stop_listener(listener, timeout=0.2) is True, "a second call must not wait again"
    finally:
        release.set()


@pytest.mark.unit
def test_stop_listener_writes_out_queued_records(tmp_path: Path) -> None:
    log_file = tmp_path / "ok.log"
    log_queue: mp.Queue[object] = mp.Queue()
    listener = start_listener(log_queue, log_file, "DEBUG")
    build_worker_logger(log_queue, name="test-stop-listener").warning("last words")

    assert stop_listener(listener) is True
    assert "last words" in log_file.read_text()


@pytest.mark.unit
def test_start_listener_writes_non_ascii_records(tmp_path: Path) -> None:
    """A record containing non-ASCII text must not raise UnicodeEncodeError.

    Regression test: FileHandler's default encoding is the platform's
    preferred locale encoding, not UTF-8.  On Windows that raised
    UnicodeEncodeError in the listener thread for the first non-ASCII
    character logged (e.g. a scanned path), silently dropping the record.
    """
    # U+2603 SNOWMAN and U+65E5 (日) are outside cp1252/latin-1 and every other
    # common single-byte Windows code page, so this reliably reproduces the
    # crash under the platform's default locale encoding, unlike characters
    # such as é/ü that cp1252 happens to cover.
    message = "café ☃ 日本語"
    log_file = tmp_path / "unicode.log"
    log_queue: mp.Queue[object] = mp.Queue()
    listener = start_listener(log_queue, log_file, "DEBUG")
    build_worker_logger(log_queue, name="test-non-ascii").warning(message)

    assert stop_listener(listener) is True
    assert message in log_file.read_text(encoding="utf-8")


@pytest.mark.unit
def test_start_listener_truncates_log_file_on_each_call(tmp_path: Path) -> None:
    """Each run starts with a fresh log file — mode="w", not the default "a"."""
    log_file = tmp_path / "truncate.log"

    log_queue: mp.Queue[object] = mp.Queue()
    listener = start_listener(log_queue, log_file, "DEBUG")
    build_worker_logger(log_queue, name="test-truncate-1").warning("first run")
    assert stop_listener(listener) is True
    assert "first run" in log_file.read_text()

    log_queue = mp.Queue()
    listener = start_listener(log_queue, log_file, "DEBUG")
    build_worker_logger(log_queue, name="test-truncate-2").warning("second run")
    assert stop_listener(listener) is True

    content = log_file.read_text()
    assert "second run" in content
    assert "first run" not in content, "log file was appended to instead of truncated"
