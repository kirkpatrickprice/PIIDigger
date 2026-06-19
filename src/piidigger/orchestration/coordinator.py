from __future__ import annotations

import logging
import logging.handlers
import multiprocessing as mp
import queue
import time
from typing import Any

from piidigger.models.tasks import Task, TaskResult, TaskStarted, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger, start_listener, stop_listener
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.worker import broadcast_shutdown, join_workers, worker_loop

# How often (seconds) the coordinator checks for worker deadline violations
# when the result queue is empty.
HEARTBEAT_CHECK_INTERVAL: float = 1.0


def run_coordinator(
    ctx: WorkerContext,
    workers: list[mp.Process],
    listener: logging.handlers.QueueListener,
    sinks: list[Any],
    progress: ProgressDisplay,
) -> None:
    """Drive the fan-out scan loop until all work is accounted for.

    Seeds one ENUM_DIR task per config.start_dirs, then drains result_queue
    and re-enqueues any new tasks discovered by workers.  Terminates when
    pending == 0 (every enqueued task has produced exactly one result).

    Post-loop teardown (broadcast_shutdown, join_workers, flush_sinks,
    stop_listener) runs in a finally block so it always executes — both on
    normal completion and on KeyboardInterrupt.

    Args:
        ctx: Shared context (queues, config, stop_event) for workers.
        workers: Live worker processes — updated in-place on replacement.
        listener: Logging QueueListener started before this call; stopped here.
        sinks: OutputSink instances to receive findings (empty list in Phase 2).
        progress: Progress display owned by this coordinator.
    """
    logger = build_worker_logger(ctx.log_queue, "coordinator")

    # task_id → Task for all tasks that have been enqueued but not yet completed.
    # Used to look up timeout_seconds when a TaskStarted heartbeat arrives.
    _pending_tasks: dict[str, Task] = {}

    # task_id → (worker_pid, dispatch_monotonic, timeout_seconds) for in-flight tasks
    # (TaskStarted received but TaskResult not yet received).
    _in_flight: dict[str, tuple[int, float, int]] = {}

    # pid → Process for fast lookup during deadline termination.
    _pid_to_proc: dict[int, mp.Process] = {p.pid: p for p in workers if p.pid is not None}

    def _enqueue(task: Task) -> None:
        ctx.task_queue.put(task)
        _pending_tasks[task.task_id] = task

    def _record_heartbeat(msg: TaskStarted) -> None:
        task = _pending_tasks.get(msg.task_id)
        if task is None:
            logger.warning("heartbeat for unknown task %s — ignoring", msg.task_id)
            return
        _in_flight[msg.task_id] = (msg.worker_pid, time.monotonic(), task.timeout_seconds)

    def _check_worker_deadlines(pending: int) -> int:
        """Scan in-flight tasks for deadline violations; synthesise timeout results.

        Returns the updated pending count after any synthesised results.

        Phase 4 extension point: add crash-before-heartbeat detection here —
        check for worker processes that are dead (not proc.is_alive()) but whose
        task_id has no entry in _in_flight (never sent a heartbeat).  Re-queue
        those tasks up to MAX_RETRIES times before synthesising an error result.
        """
        now = time.monotonic()
        timed_out: list[str] = []

        for task_id, (pid, dispatch_time, timeout_seconds) in _in_flight.items():
            if now - dispatch_time > 2 * timeout_seconds:
                timed_out.append(task_id)
                logger.warning(
                    "deadline exceeded: task=%s pid=%d elapsed=%.1fs timeout=%ds",
                    task_id,
                    pid,
                    now - dispatch_time,
                    timeout_seconds,
                )

        for task_id in timed_out:
            pid, _, _ = _in_flight.pop(task_id)
            _pending_tasks.pop(task_id, None)

            # Terminate the hung worker and spawn a replacement
            old_proc = _pid_to_proc.pop(pid, None)
            if old_proc is not None:
                old_proc.terminate()
                old_proc.join(timeout=2.0)
                # Remove from the shared workers list so join_workers sees the replacement
                if old_proc in workers:
                    workers.remove(old_proc)

            new_proc = mp.Process(target=worker_loop, args=(ctx,))
            new_proc.start()
            if new_proc.pid is not None:
                _pid_to_proc[new_proc.pid] = new_proc
            workers.append(new_proc)
            logger.info("spawned replacement worker pid=%s", new_proc.pid)

            progress.log_event("WARNING", f"timeout task={task_id}")
            pending -= 1

        return pending

    def _route_to_sinks(findings: list[dict[str, Any]], output_sinks: list[Any]) -> None:
        """Forward findings to each OutputSink.  No-op when sinks list is empty (Phase 2)."""
        for finding in findings:
            for sink in output_sinks:
                sink.write(finding)

    def _flush_sinks(output_sinks: list[Any]) -> None:
        """Close all output sinks after the coordinator loop exits."""
        for sink in output_sinks:
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                logger.exception("error closing sink %r", sink)

    # ------------------------------------------------------------------
    # Seed initial tasks — one ENUM_DIR per configured start directory
    # ------------------------------------------------------------------
    pending = 0
    for path in ctx.config.start_dirs:
        task = Task(task_type=TaskType.ENUM_DIR, payload={"path": str(path), "depth": 0})
        _enqueue(task)
        pending += 1

    logger.info("coordinator seeded %d initial task(s)", pending)

    # ------------------------------------------------------------------
    # Main fan-out loop
    # ------------------------------------------------------------------
    _interrupted = False
    try:
        while pending > 0:
            try:
                raw: Any = ctx.result_queue.get(timeout=HEARTBEAT_CHECK_INTERVAL)
            except queue.Empty:
                pending = _check_worker_deadlines(pending)
                continue

            if isinstance(raw, TaskStarted):
                _record_heartbeat(raw)
                continue

            if not isinstance(raw, TaskResult):
                logger.warning("coordinator received unexpected message type %s", type(raw).__name__)
                continue

            result: TaskResult = raw
            _in_flight.pop(result.task_id, None)
            _pending_tasks.pop(result.task_id, None)
            pending -= 1

            if result.status == "error":
                logger.error(
                    "task %s failed: %s",
                    result.task_id,
                    result.error_message or "(no message)",
                )

            for new_task_dict in result.new_tasks:
                new_task = Task(**new_task_dict)
                _enqueue(new_task)
                pending += 1

            _route_to_sinks(result.findings, sinks)
            progress.update(result.counters)

        logger.info("coordinator: all tasks complete (pending=0)")

    except KeyboardInterrupt:
        _interrupted = True
        logger.warning("scan interrupted by user (KeyboardInterrupt)")

    finally:
        broadcast_shutdown(ctx.task_queue, len(workers))
        if _interrupted:
            # Workers may be mid-task and won't drain the shutdown queue promptly;
            # terminate immediately so the process exits within the expected window.
            for p in workers:
                if p.is_alive():
                    p.terminate()
        join_workers(workers, timeout=5.0, logger=logger)
        _flush_sinks(sinks)
        stop_listener(listener)
        progress.stop()


# ---------------------------------------------------------------------------
# Test-only subprocess entry point
# ---------------------------------------------------------------------------


def _run_with_internal_workers(
    ctx: WorkerContext,
    n_workers: int,
    log_file_str: str,
) -> None:
    """Start workers internally and run the coordinator.

    Defined in this module (not in tests/) so Windows mp.spawn can import it.
    Test code that needs to interrupt the coordinator subprocess uses this as
    the mp.Process target — spawned processes can only import from installed
    packages, not from the test directory.

    TEST-ONLY: do not call from production code.
    """
    from pathlib import Path

    from piidigger.orchestration.worker import start_worker_pool  # local: avoids circular at module level

    listener = start_listener(ctx.log_queue, Path(log_file_str), "DEBUG")
    workers = start_worker_pool(ctx, n_workers)
    progress = ProgressDisplay()
    progress._is_tty = False
    run_coordinator(ctx, workers, listener, [], progress)
