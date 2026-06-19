from __future__ import annotations

import logging
import multiprocessing as mp
import os
import time
from collections.abc import Callable
from typing import Any

from piidigger.models.tasks import SHUTDOWN, ShutdownSentinel, Task, TaskResult, TaskStarted, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger

# ---------------------------------------------------------------------------
# Handler type alias and dispatch table
# ---------------------------------------------------------------------------

type _HandlerFn = Callable[[Task, WorkerContext, logging.Logger], TaskResult]


def _handle_noop(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Return an ok result immediately; used only for integration testing."""
    logger.debug("noop task %s", task.task_id)
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        worker_pid=os.getpid(),
    )


# ---------------------------------------------------------------------------
# Phase 2 stubs — replace with real filesystem handlers in Phase 3
# ---------------------------------------------------------------------------


def _handle_enum_dir_stub(task: Task, _ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Synthetic directory enumeration — no filesystem I/O.

    depth == 0: fans out to 2 child ENUM_DIR tasks (depth=1) + 3 SCAN_FILE tasks.
    depth >= 1: leaf node; returns only a dirs_scanned counter.

    Special path prefix "/slow_test/": returns one SLOW_TEST task (timeout=60s)
    and no child dirs.  Used by the Ctrl+C integration test to guarantee the
    coordinator is blocked on result_queue.get() when SIGINT is sent.

    Replace with handle_enum_dir() in Phase 3.
    """
    path: str = str(task.payload.get("path", ""))
    depth: int = int(task.payload.get("depth", 0))
    logger.debug("enum_dir stub depth=%d task=%s", depth, task.task_id)

    if path.startswith("/slow_test/"):
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="ok",
            new_tasks=[{"task_type": TaskType.SLOW_TEST, "payload": {}, "timeout_seconds": 60}],
            counters={"dirs_scanned": 1},
            worker_pid=os.getpid(),
        )

    if depth >= 1:
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="ok",
            counters={"dirs_scanned": 1},
            worker_pid=os.getpid(),
        )

    new_tasks: list[dict[str, Any]] = [
        {"task_type": TaskType.ENUM_DIR, "payload": {"path": "/synthetic/child/1", "depth": 1}},
        {"task_type": TaskType.ENUM_DIR, "payload": {"path": "/synthetic/child/2", "depth": 1}},
        {"task_type": TaskType.SCAN_FILE, "payload": {"path": "/synthetic/file1.txt"}},
        {"task_type": TaskType.SCAN_FILE, "payload": {"path": "/synthetic/file2.txt"}},
        {"task_type": TaskType.SCAN_FILE, "payload": {"path": "/synthetic/file3.txt"}},
    ]
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        new_tasks=new_tasks,
        counters={"dirs_found": 2, "files_found": 3, "dirs_scanned": 1},
        worker_pid=os.getpid(),
    )


def _handle_scan_file_stub(task: Task, _ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Synthetic file scan — no filesystem I/O.

    Returns fixed counters only.  Replace with handle_scan_file() in Phase 3.
    """
    logger.debug("scan_file stub task=%s", task.task_id)
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        counters={"files_scanned": 1, "bytes_scanned": 1024},
        worker_pid=os.getpid(),
    )


def _handle_slow_test(task: Task, _ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Sleep for 120 seconds to trigger coordinator deadline detection.

    TEST-ONLY — used exclusively by test_deadline_detection_terminates_hung_worker.
    Remove from DISPATCH in Phase 3 when real handlers replace all stubs.
    """
    logger.debug("slow_test sleeping task=%s", task.task_id)
    time.sleep(120)
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        worker_pid=os.getpid(),
    )


# Phase 2 stubs in DISPATCH — ENUM_DIR/SCAN_FILE/SLOW_TEST replaced by real handlers in Phase 3
DISPATCH: dict[TaskType, _HandlerFn] = {
    TaskType.NOOP: _handle_noop,
    TaskType.ENUM_DIR: _handle_enum_dir_stub,
    TaskType.SCAN_FILE: _handle_scan_file_stub,
    TaskType.SLOW_TEST: _handle_slow_test,  # test-only; remove in Phase 3
}

# ---------------------------------------------------------------------------
# Worker internals
# ---------------------------------------------------------------------------


def _cleanup_temp_workspace() -> None:
    """Remove per-task temp files after each task completes.

    No-op in Phase 1.  Phase 5 (archive support) will populate this:
    each task that calls ScannableItem.materialize() creates a temp
    workspace directory here.  Cleanup uses the secure-deletion library
    selected in Open Decision 6 (overwrite-then-delete, not os.unlink).
    """


def _dispatch(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Call the registered handler; convert any exception to a status='error' result."""
    handler = DISPATCH.get(task.task_type)
    if handler is None:
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=f"no handler registered for task_type={task.task_type!r}",
            worker_pid=os.getpid(),
        )
    start = time.monotonic()
    try:
        result = handler(task, ctx, logger)
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled error in handler for task %s", task.task_id)
        result = TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            duration_seconds=time.monotonic() - start,
            worker_pid=os.getpid(),
        )
    else:
        result = result.model_copy(update={"duration_seconds": time.monotonic() - start})
    return result


# ---------------------------------------------------------------------------
# Worker loop (process target)
# ---------------------------------------------------------------------------


def worker_loop(ctx: WorkerContext) -> None:
    """Main loop for each worker process.

    Pulls tasks from ctx.task_queue, dispatches them, and puts results on
    ctx.result_queue.  Exits cleanly on ShutdownSentinel or KeyboardInterrupt.
    """
    logger = build_worker_logger(ctx.log_queue, f"worker-{os.getpid()}")
    logger.debug("worker started (pid=%d)", os.getpid())

    try:
        while not ctx.stop_event.is_set():
            item: Any = ctx.task_queue.get()
            if isinstance(item, ShutdownSentinel):
                logger.debug("received SHUTDOWN; exiting")
                break
            task: Task = item
            ctx.result_queue.put(TaskStarted(task_id=task.task_id, worker_pid=os.getpid()))
            try:
                result = _dispatch(task, ctx, logger)
            finally:
                _cleanup_temp_workspace()
            ctx.result_queue.put(result)
    except KeyboardInterrupt:
        logger.debug("worker interrupted; exiting after current task")

    logger.debug("worker stopped (pid=%d)", os.getpid())


# ---------------------------------------------------------------------------
# Worker pool helpers
# ---------------------------------------------------------------------------


def start_worker_pool(ctx: WorkerContext, n_workers: int) -> list[mp.Process]:
    """Spawn n_workers processes running worker_loop and return them."""
    workers: list[mp.Process] = []
    for _ in range(n_workers):
        p = mp.Process(target=worker_loop, args=(ctx,))
        p.start()
        workers.append(p)
    return workers


def broadcast_shutdown(task_queue: mp.Queue[Any], n_workers: int) -> None:
    """Put one ShutdownSentinel per worker onto task_queue."""
    for _ in range(n_workers):
        task_queue.put(SHUTDOWN)


def join_workers(
    workers: list[mp.Process],
    timeout: float = 5.0,
    logger: logging.Logger | None = None,
) -> None:
    """Join all workers; force-terminate any still alive after timeout.

    timeout is a total wall-clock budget shared across all workers, not a
    per-worker limit — so N workers don't multiply the wait.
    """
    log = logger or logging.getLogger(__name__)
    deadline = time.monotonic() + timeout
    for proc in workers:
        proc.join(timeout=max(0.0, deadline - time.monotonic()))
    stragglers = [p for p in workers if p.is_alive()]
    for proc in stragglers:
        log.warning(
            "worker PID %d did not exit within %.1fs; terminating",
            proc.pid,
            timeout,
        )
        proc.terminate()
    for proc in stragglers:
        proc.join(timeout=2.0)
