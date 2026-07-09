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
from piidigger.orchestration.worker import MAX_RETRIES, broadcast_shutdown, join_workers, worker_loop

# How often (seconds) the coordinator checks for worker deadline violations
# when the result queue is empty.
HEARTBEAT_CHECK_INTERVAL: float = 1.0

# A task that has been in _pending_tasks but not in _in_flight (no heartbeat)
# for longer than this many seconds is a candidate for crash-orphan re-queue,
# provided at least one worker process has died unexpectedly.
_CRASH_DETECT_TIMEOUT: float = 30.0

_ACCESS_DENIED_PHRASES: tuple[str, ...] = ("Access is denied", "Permission denied", "WinError 5", "Errno 13")


def _truncate_path(path: str, max_len: int = 60) -> str:
    """Truncate path to max_len chars, keeping the filename and as much of the start as fits."""
    if len(path) <= max_len:
        return path
    sep = "\\" if "\\" in path else "/"
    if sep in path:
        filename = path.rsplit(sep, 1)[1]
        tail = sep + filename
        head_len = max_len - len(tail) - 3  # 3 for "..."
        if head_len > 0:
            return f"{path[:head_len]}...{tail}"
    return path[: max_len - 3] + "..."


def _is_access_denied(error_message: str) -> bool:
    return any(phrase in error_message for phrase in _ACCESS_DENIED_PHRASES)


def _denied_path(error_message: str) -> str:
    """Extract the filesystem path from an OS access-denied error string."""
    if ": '" in error_message:
        return error_message.rsplit(": '", 1)[-1].rstrip("'")
    return error_message


def _short_error(error_message: str) -> str:
    """Condense an error message for display, stripping any embedded file path."""
    first_line = error_message.split("\n")[0]
    # OS errors end with ": 'path'" — strip that since the path is shown separately
    if ": '" in first_line:
        first_line = first_line.rsplit(": '", 1)[0]
    return first_line[:80]


def _findings_summary(findings: list[dict[str, Any]]) -> str:
    """Return 'truncated/path — HANDLER: N  HANDLER: N' for a list of ResultRecord dicts."""
    if not findings:
        return ""
    source_path = findings[0].get("source_path", "")
    handler_counts: dict[str, int] = {}
    for f in findings:
        name = f.get("handler", "?")
        count = sum(len(v) for v in f.get("matches", {}).values())
        handler_counts[name] = handler_counts.get(name, 0) + count
    counts = "  ".join(f"{n.upper()}: {c}" for n, c in sorted(handler_counts.items()))
    return f"{_truncate_path(source_path)} — {counts}"


def _task_path(task: Task | None) -> str:
    """Return the most human-readable path from a task's payload, or '' if unavailable."""
    if task is None:
        return ""
    p = task.payload
    if task.task_type == TaskType.SCAN_FILE:
        return str(p.get("display_path", p.get("file_path", "")))
    if task.task_type == TaskType.ENUM_DIR:
        return str(p.get("path", ""))
    if task.task_type == TaskType.ENUM_ARCHIVE_MEMBERS:
        return str(p.get("archive_path", ""))
    if task.task_type == TaskType.SCAN_ARCHIVE_MEMBER:
        archive = p.get("archive_path", "")
        member = p.get("member_path", "")
        return f"{archive}::{member}" if member else str(archive)
    return ""


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

    # task_id → monotonic time when the task was enqueued; used to detect
    # tasks that have been waiting too long without a heartbeat (crash orphans).
    _task_enqueue_time: dict[str, float] = {}

    # task_id → number of times this task has been re-queued after a crash.
    _task_retries: dict[str, int] = {}

    def _enqueue(task: Task, retry_count: int = 0) -> None:
        ctx.task_queue.put(task)
        _pending_tasks[task.task_id] = task
        _task_enqueue_time[task.task_id] = time.monotonic()
        _task_retries[task.task_id] = retry_count

    def _record_heartbeat(msg: TaskStarted) -> None:
        task = _pending_tasks.get(msg.task_id)
        if task is None:
            logger.warning("heartbeat for unknown task %s — ignoring", msg.task_id)
            return
        _in_flight[msg.task_id] = (msg.worker_pid, time.monotonic(), task.timeout_seconds)

    def _check_worker_deadlines(pending: int) -> int:
        """Scan for deadline violations and crashed workers; synthesise results as needed.

        Returns the updated pending count after any synthesised results.

        Two detection paths:
        1. Timeout: task in _in_flight past 2 × timeout_seconds — terminate worker,
           spawn replacement, synthesise status='timeout', decrement pending.
        2. Crash-before-heartbeat: worker dead but its task has no heartbeat entry —
           re-queue up to MAX_RETRIES times; after that synthesise status='error'.
        """
        now = time.monotonic()
        timed_out: list[str] = []

        for task_id, (_pid, dispatch_time, timeout_seconds) in _in_flight.items():
            if now - dispatch_time > 2 * timeout_seconds:
                timed_out.append(task_id)

        for task_id in timed_out:
            pid, dispatch_time, timeout_sec = _in_flight.pop(task_id)
            task = _pending_tasks.pop(task_id, None)
            _task_enqueue_time.pop(task_id, None)
            _task_retries.pop(task_id, None)
            elapsed = now - dispatch_time

            task_type_str = task.task_type.value if task is not None else "unknown"
            source_path = _task_path(task)

            logger.warning(
                "deadline exceeded: type=%s path=%r pid=%d elapsed=%.1fs"
                " timeout=%ds — worker terminated; replacement spawned",
                task_type_str,
                source_path,
                pid,
                elapsed,
                timeout_sec,
            )

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

            short_path = _truncate_path(source_path) if source_path else f"task {task_id[:8]}…"
            progress.log_event(
                "WARNING",
                f"Timeout [{task_type_str}] {short_path}"
                f" — pid={pid}, {elapsed:.0f}s/{timeout_sec}s, replacement spawned",
            )
            pending -= 1

        # --- Crash-before-heartbeat detection ---
        # A worker that died without our explicit terminate() may have dequeued a
        # task before crashing, leaving that task orphaned (no heartbeat, no result).
        dead_pids = [pid for pid, proc in _pid_to_proc.items() if not proc.is_alive()]
        if dead_pids:
            for pid in dead_pids:
                old_proc = _pid_to_proc.pop(pid)
                if old_proc in workers:
                    workers.remove(old_proc)
                new_proc = mp.Process(target=worker_loop, args=(ctx,))
                new_proc.start()
                if new_proc.pid is not None:
                    _pid_to_proc[new_proc.pid] = new_proc
                workers.append(new_proc)
                logger.warning("worker pid=%d crashed; replacement pid=%s", pid, new_proc.pid)
                progress.log_event("WARNING", f"Worker pid={pid} crashed unexpectedly")

            # Any task pending without a heartbeat for > _CRASH_DETECT_TIMEOUT may
            # have been dequeued by the crashed worker.  Re-queue up to MAX_RETRIES.
            crash_orphans = [
                task_id
                for task_id in _pending_tasks
                if task_id not in _in_flight and now - _task_enqueue_time.get(task_id, now) > _CRASH_DETECT_TIMEOUT
            ]
            for task_id in crash_orphans:
                task = _pending_tasks.pop(task_id)
                _task_enqueue_time.pop(task_id, None)
                retries = _task_retries.pop(task_id, 0)
                pending -= 1

                if retries < MAX_RETRIES:
                    new_task = Task(
                        task_type=task.task_type,
                        payload=task.payload,
                        timeout_seconds=task.timeout_seconds,
                    )
                    logger.warning(
                        "crash-orphan: re-queuing %s as %s (retry %d/%d)",
                        task_id,
                        new_task.task_id,
                        retries + 1,
                        MAX_RETRIES,
                    )
                    _enqueue(new_task, retries + 1)
                    pending += 1
                else:
                    logger.error(
                        "crash-orphan: task %s exceeded MAX_RETRIES=%d; dropping",
                        task_id,
                        MAX_RETRIES,
                    )
                    progress.log_event(
                        "ERROR",
                        f"Crash orphan: task={task_id[:8]}… exceeded {MAX_RETRIES} retries",
                    )

        return pending

    def _route_to_sinks(findings: list[dict[str, Any]], output_sinks: list[Any]) -> None:
        """Forward findings to each OutputSink.  No-op when sinks list is empty (Phase 2).

        Findings cross the process boundary as plain dicts (picklable); sinks
        expect validated ResultRecord objects, so we reconstitute them here at
        the coordinator boundary.
        """
        from piidigger.models.results import ResultRecord  # local: avoids circular at module level

        for finding_dict in findings:
            try:
                record = ResultRecord.model_validate(finding_dict)
            except Exception:  # noqa: BLE001
                logger.warning("coordinator: could not deserialize finding: %r", finding_dict)
                continue
            for sink in output_sinks:
                sink.write(record)

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

    # Pre-seed dirs_found so the progress bar starts at "0 / N" rather than
    # "0 / 0".  Each ENUM_DIR result will add its discovered subdirs to the
    # total, so the bar reaches 100% when all dirs have been scanned.
    progress.update({"dirs_found": len(ctx.config.start_dirs)})

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
            pending_task = _pending_tasks.pop(result.task_id, None)
            _task_enqueue_time.pop(result.task_id, None)
            _task_retries.pop(result.task_id, None)
            pending -= 1

            if result.status == "error":
                msg = result.error_message or "(no message)"
                err_path = _task_path(pending_task)
                logger.error(
                    "[%s]%s: %s",
                    result.task_type.value,
                    f" path={err_path!r}" if err_path else "",
                    msg,
                )
                if _is_access_denied(msg):
                    progress.log_event("WARNING", f"Access denied: {_truncate_path(_denied_path(msg))}")
                elif result.task_type == TaskType.SCAN_FILE:
                    file_path = pending_task.payload.get("display_path", "") if pending_task else ""
                    progress.log_event("ERROR", f"Error: {_truncate_path(file_path)} — {_short_error(msg)}")

            for new_task_dict in result.new_tasks:
                new_task = Task(**new_task_dict)
                _enqueue(new_task)
                pending += 1

            _route_to_sinks(result.findings, sinks)
            if result.findings:
                progress.log_event("INFO", _findings_summary(result.findings))
            # Merge ETA counters with the result's own counters in one call so
            # the display refreshes once per result.  tasks_pending is the
            # post-adjustment snapshot (after new tasks were enqueued above).
            progress.update({**result.counters, "tasks_completed": 1, "tasks_pending": pending})

        logger.info("coordinator: all tasks complete (pending=0)")

    except KeyboardInterrupt:
        _interrupted = True
        logger.warning("scan interrupted by user (KeyboardInterrupt)")
        progress.log_event(
            "WARNING",
            "Scan interrupted — shutting down gracefully  (CTRL-C again to force-quit)",
        )

    finally:
        if _interrupted:
            # Cancel feeder-thread joins NOW, before any teardown step that could
            # block.  If a second CTRL-C breaks out of this finally block, the
            # atexit handler will see _joincancelled=True and skip thread.join(),
            # so no unhandled KeyboardInterrupt from the multiprocessing atexit hook.
            ctx.task_queue.cancel_join_thread()
            ctx.result_queue.cancel_join_thread()
            ctx.log_queue.cancel_join_thread()
            for p in workers:
                if p.is_alive():
                    p.terminate()
        else:
            # Graceful completion: signal workers to exit cleanly.
            broadcast_shutdown(ctx.task_queue, len(workers))

        try:
            if _interrupted:
                progress.log_event("INFO", "Waiting for workers to stop…")
            join_workers(workers, timeout=2.0 if _interrupted else 5.0, logger=logger)

            if _interrupted:
                progress.log_event("INFO", "Saving results to output files…")
            _flush_sinks(sinks)
            stop_listener(listener)

        except KeyboardInterrupt:
            # Second CTRL-C: force-quit without waiting for clean teardown.
            for p in workers:
                if p.is_alive():
                    p.terminate()
            ctx.task_queue.cancel_join_thread()
            ctx.result_queue.cancel_join_thread()
            ctx.log_queue.cancel_join_thread()
            progress.log_event("WARNING", "Force-quit — remaining output abandoned")

        finally:
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
