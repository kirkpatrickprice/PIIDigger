from __future__ import annotations

import logging
import multiprocessing as mp
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from piidigger.models.tasks import (
    SHUTDOWN,
    ShutdownSentinel,
    Task,
    TaskResult,
    TaskStarted,
    TaskType,
    WorkerReady,
    WorkerRetiring,
)
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger, route_library_logs, setup_warning_capture
from piidigger.orchestration.secure_delete import secure_rmtree
from piidigger.orchestration.worker._enum_archive import handle_enum_archive_members
from piidigger.orchestration.worker._enum_dir import handle_enum_dir
from piidigger.orchestration.worker._scan_archive_members import handle_scan_archive_members
from piidigger.orchestration.worker._scan_file import handle_scan_file

type _HandlerFn = Callable[[Task, WorkerContext, logging.Logger], TaskResult]


def _handle_noop(task: Task, _ctx: WorkerContext, logger: logging.Logger) -> TaskResult:  # noqa: ARG001
    """Return an ok result; used only for integration testing.

    Pass {"delay_seconds": N} in the task payload to simulate a slow task for
    deadline-detection tests.  This replaces the removed SLOW_TEST task type.
    """
    delay = float(task.payload.get("delay_seconds", 0))
    if delay > 0:
        logger.debug("noop task %s sleeping %.1fs", task.task_id, delay)
        time.sleep(delay)
    else:
        logger.debug("noop task %s", task.task_id)
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        worker_pid=os.getpid(),
    )


DISPATCH: dict[TaskType, _HandlerFn] = {
    TaskType.NOOP: _handle_noop,
    TaskType.ENUM_DIR: handle_enum_dir,
    TaskType.SCAN_FILE: handle_scan_file,
    TaskType.ENUM_ARCHIVE_MEMBERS: handle_enum_archive_members,
    TaskType.SCAN_ARCHIVE_MEMBERS: handle_scan_archive_members,
}


def _cleanup_temp_workspace(workspace: Path, logger: logging.Logger) -> None:
    """Securely delete this task's temp files then remove its temp directory.

    Walks the task temp dir recursively so handlers need not flatten extracted
    files to a single level.  No-ops gracefully when the task created no temp
    files.

    Never raises: it runs in worker_loop's finally block, and an exception there
    would kill the worker and lose the task's result.  A file that cannot be
    removed yet (an antivirus or indexer lock on Windows) is logged and left
    for run_scan's own secure_rmtree of temp_base, which runs at the end of the
    run.  That backstop also covers a worker stopped by terminate(), which
    never unwinds this finally block.
    """
    for path in secure_rmtree(workspace):
        logger.warning("could not remove temp file %s yet; it is removed when the scan ends", path)


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


# A worker replaces itself once its memory after a task is above this share of
# max_worker_memory_mb.  CPython seldom returns freed memory to the OS, so a
# worker that has just read a large file stays large while idle.  The worker
# exits between tasks, never while waiting in task_queue.get(): a process killed
# there leaves the queue's read lock held, and every other worker blocks on it
# for good.  The margin keeps a waiting worker below the limit at which the
# coordinator's memory sweep stops workers, so the sweep never stops one.
_RETIRE_SHARE: float = 0.95


def _rss_if_retiring(process: psutil.Process, ctx: WorkerContext) -> int | None:
    """This worker's RSS if it is high enough that the worker should retire, else None."""
    try:
        rss = int(process.memory_info().rss)
    except psutil.Error:
        return None
    threshold = _RETIRE_SHARE * ctx.config.max_worker_memory_mb * 1024 * 1024
    return rss if rss > threshold else None


def worker_loop(ctx: WorkerContext) -> None:
    """Main loop for each worker process.

    Pulls tasks from ctx.task_queue, dispatches them, and puts results on
    ctx.result_queue.  Exits cleanly on ShutdownSentinel, on KeyboardInterrupt,
    on taking any item once ctx.stop_event is set, or after a task that left it
    near max_worker_memory_mb (see _RETIRE_SHARE).
    """
    process = psutil.Process()
    logger = build_worker_logger(ctx.log_queue, f"worker-{os.getpid()}")
    setup_warning_capture(ctx.log_queue)
    route_library_logs(ctx.log_queue)
    logger.debug("worker started (pid=%d)", os.getpid())
    # Say once that this worker is up.  After this, a worker that is not running
    # a task can only be waiting in task_queue.get(); before it, silence could
    # just as well mean "still starting".  The lost-task sweep relies on that.
    ctx.result_queue.put(WorkerReady(worker_pid=os.getpid()))

    try:
        while not ctx.stop_event.is_set():
            item: Any = ctx.task_queue.get()
            if isinstance(item, ShutdownSentinel):
                logger.debug("received SHUTDOWN; exiting")
                break
            if ctx.stop_event.is_set():
                # Teardown has begun, so nothing still in the queue is wanted:
                # it is a leftover re-dispatched copy, or work the user
                # interrupted.  Running it would only delay shutdown.
                logger.debug("stop requested; dropping queued task and exiting")
                break
            task: Task = item
            ctx.result_queue.put(TaskStarted(task_id=task.task_id, worker_pid=os.getpid()))
            try:
                result = _dispatch(task, ctx, logger)
            finally:
                _cleanup_temp_workspace(ctx.task_workspace(task.task_id), logger)
            ctx.result_queue.put(result)
            if (rss := _rss_if_retiring(process, ctx)) is not None:
                ctx.result_queue.put(WorkerRetiring(worker_pid=os.getpid(), rss=rss))
                logger.debug("retiring at %d MB to free memory (pid=%d)", rss // (1024 * 1024), os.getpid())
                break
    except KeyboardInterrupt:
        logger.debug("worker interrupted; exiting after current task")

    logger.debug("worker stopped (pid=%d)", os.getpid())


def broadcast_shutdown(task_queue: mp.Queue[Any], n_workers: int) -> None:
    """Put one ShutdownSentinel per worker onto task_queue."""
    for _ in range(n_workers):
        task_queue.put(SHUTDOWN)
