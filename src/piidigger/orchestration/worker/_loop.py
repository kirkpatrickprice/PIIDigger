from __future__ import annotations

import logging
import multiprocessing as mp
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from piidigger.models.tasks import SHUTDOWN, ShutdownSentinel, Task, TaskResult, TaskStarted, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger, setup_warning_capture
from piidigger.orchestration.secure_delete import secure_delete
from piidigger.orchestration.worker._enum_archive import handle_enum_archive_members
from piidigger.orchestration.worker._enum_dir import handle_enum_dir
from piidigger.orchestration.worker._scan_archive_member import handle_scan_archive_member
from piidigger.orchestration.worker._scan_file import handle_scan_file

type _HandlerFn = Callable[[Task, WorkerContext, logging.Logger], TaskResult]

# Maximum number of times a crash-orphaned task is re-queued before the
# coordinator synthesises a permanent error result for it.
MAX_RETRIES: int = 3


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
    TaskType.SCAN_ARCHIVE_MEMBER: handle_scan_archive_member,
}


def _cleanup_temp_workspace(temp_base: Path, task_id: str) -> None:
    """Securely delete per-task temp files then remove the task temp directory.

    Walks task_temp recursively so handlers need not flatten extracted files
    to a single level.  secure_delete() is called only on files — unlink()
    raises IsADirectoryError on directories, which missing_ok=True does not
    suppress.  shutil.rmtree() removes the now-empty directory tree.
    No-ops gracefully when the task created no temp files.
    """
    task_temp = temp_base / task_id
    if not task_temp.exists():
        return
    for path in task_temp.rglob("*"):
        if path.is_file():
            secure_delete(path)
    shutil.rmtree(task_temp, ignore_errors=True)


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


def worker_loop(ctx: WorkerContext) -> None:
    """Main loop for each worker process.

    Pulls tasks from ctx.task_queue, dispatches them, and puts results on
    ctx.result_queue.  Exits cleanly on ShutdownSentinel or KeyboardInterrupt.
    """
    logger = build_worker_logger(ctx.log_queue, f"worker-{os.getpid()}")
    setup_warning_capture(ctx.log_queue)
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
                _cleanup_temp_workspace(ctx.temp_base, task.task_id)
            ctx.result_queue.put(result)
    except KeyboardInterrupt:
        logger.debug("worker interrupted; exiting after current task")

    logger.debug("worker stopped (pid=%d)", os.getpid())


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
