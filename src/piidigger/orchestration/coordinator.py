from __future__ import annotations

import logging
import logging.handlers
import queue
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from piidigger.models.config import Config
from piidigger.models.tasks import Task, TaskResult, TaskStarted, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger, start_listener, stop_listener
from piidigger.orchestration.pool import WorkerPool, spawn_worker
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.registry import TaskRecord, TaskRegistry
from piidigger.orchestration.worker import broadcast_shutdown

# How often (seconds) the coordinator runs its health sweep.  The sweep is
# scheduled by elapsed time, not by the result queue going quiet, so a busy scan
# is checked on the same cadence as an idle one.
HEARTBEAT_CHECK_INTERVAL: float = 1.0

# How long teardown waits for workers to exit before stopping them.
_JOIN_TIMEOUT: float = 5.0
_INTERRUPT_JOIN_TIMEOUT: float = 2.0

_ACCESS_DENIED_PHRASES: tuple[str, ...] = ("Access is denied", "Permission denied", "WinError 5", "Errno 13")


@dataclass(frozen=True)
class CoordinatorResult:
    """How the scan ended, so the caller can pick an exit code.

    The coordinator deliberately does not choose exit codes itself — that is a
    CLI concern, and run_scan owns the mapping.

    A dataclass rather than a Pydantic model: both fields are computed by
    run_coordinator from its own local state and read by run_scan in the same
    process, so there is no external input to validate and nothing to serialise.

    unfinished > 0 means the loop exited with work still outstanding.  On a clean
    run that is impossible; it is reported rather than swallowed so a truncated
    scan cannot masquerade as a successful one.
    """

    interrupted: bool = False
    unfinished: int = 0


@dataclass(frozen=True)
class SweepResult:
    """What one health sweep found and did, for the coordinator to report.

    Holds the affected records rather than bare task ids.  By the time the report
    is rendered those tasks have left the registry, and the report still needs
    each task's path and timeout.

    A dataclass rather than a Pydantic model: every field is state the monitor
    read from our own registry and pool.
    """

    now: float
    timed_out: list[TaskRecord] = field(default_factory=list)
    crashed: list[tuple[int, int | None]] = field(default_factory=list)
    redispatched: list[TaskRecord] = field(default_factory=list)
    abandoned: list[TaskRecord] = field(default_factory=list)

    def __bool__(self) -> bool:
        """True when the sweep has anything to report."""
        return bool(self.timed_out or self.crashed or self.redispatched or self.abandoned)


class HealthMonitor:
    """The coordinator's periodic health sweep.

    Kept apart from the drain loop, with its collaborators injected, so it can be
    unit-tested without spawning a process.  tick() runs two checks, and the
    order matters:

    1. Deadline sweep.  A RUNNING task past its deadline is abandoned and its
       worker replaced.  Timeouts are not retried: a task that hung once will
       most likely hang again.
    2. Crash sweep.  Workers that died unprompted are replaced.  Then every
       RUNNING task whose worker is no longer in the pool is re-dispatched, or
       abandoned once its retry budget is spent.  Matching on "not in the pool"
       rather than "died this tick" also catches a heartbeat that arrived after
       its worker had already been reaped.

    Because the deadline sweep runs first, a worker replaced for a timeout has
    already left the pool, so the crash sweep cannot replace it a second time.

    A task lost before its heartbeat is invisible to both checks, because no
    worker is recorded as holding it.
    """

    def __init__(
        self,
        registry: TaskRegistry,
        pool: WorkerPool,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._pool = pool
        self._clock = clock

    def tick(self) -> SweepResult:
        """Run one sweep and report what it did."""
        now = self._clock()

        timed_out: list[TaskRecord] = []
        for record in self._registry.expired(now):
            self._registry.abandon(record.task_id, reason="timed_out")
            if record.worker_pid is not None:
                self._pool.replace(record.worker_pid)
            timed_out.append(record)

        crashed = self._pool.reap_dead()
        live = self._pool.pids()
        redispatched: list[TaskRecord] = []
        abandoned: list[TaskRecord] = []
        for record in self._registry.running():
            if record.worker_pid in live:
                continue
            if self._registry.redispatch(record.task_id) is not None:
                redispatched.append(record)
            else:
                self._registry.abandon(record.task_id, reason="crashed")
                abandoned.append(record)

        return SweepResult(
            now=now,
            timed_out=timed_out,
            crashed=crashed,
            redispatched=redispatched,
            abandoned=abandoned,
        )


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


def build_seed_tasks(config: Config) -> list[Task]:
    """Build one ENUM_DIR task per configured start directory.

    Separate from run_coordinator so the seeding contract is testable without
    spawning processes — in particular that seeds carry
    config.default_timeout_seconds rather than the Task model's own default.
    """
    return [
        Task(
            task_type=TaskType.ENUM_DIR,
            payload={"path": str(path), "depth": 0},
            timeout_seconds=config.default_timeout_seconds,
        )
        for path in config.start_dirs
    ]


def run_coordinator(
    ctx: WorkerContext,
    pool: WorkerPool,
    listener: logging.handlers.QueueListener,
    sinks: list[Any],
    progress: ProgressDisplay,
    *,
    seed_tasks: Sequence[Task] | None = None,
) -> CoordinatorResult:
    """Drive the fan-out scan until every task is accounted for.

    Seeds the task registry, then drains the result queue.  Each result retires
    its task and may enqueue children; the loop ends when the registry is empty.
    A health sweep runs every HEARTBEAT_CHECK_INTERVAL seconds, however busy the
    result queue is.

    Teardown (stop the workers, flush the sinks, stop the listener and the
    display) runs in a finally block, on normal completion and on
    KeyboardInterrupt alike.

    Args:
        ctx: Shared context (queues, config) for workers.
        pool: Started worker pool.  The coordinator replaces workers through it
            and stops all of them at teardown.
        listener: Logging QueueListener started before this call; stopped here.
        sinks: Opened OutputSink instances that receive findings; closed here.
        progress: Progress display owned by this coordinator; stopped here.
        seed_tasks: Initial tasks.  Defaults to one ENUM_DIR per
            config.start_dirs.  Tests pass their own to drive particular task
            types through the real loop.

    Returns:
        CoordinatorResult describing how the run ended, for exit-code mapping.
    """
    logger = build_worker_logger(ctx.log_queue, "coordinator")
    registry = TaskRegistry(ctx.task_queue.put)
    monitor = HealthMonitor(registry, pool)

    seeds = build_seed_tasks(ctx.config) if seed_tasks is None else list(seed_tasks)
    # Pre-seed dirs_found so the progress bar starts at "0 / N" rather than
    # "0 / 0".  Each ENUM_DIR result adds the subdirectories it discovers.
    progress.update({"dirs_found": sum(1 for t in seeds if t.task_type == TaskType.ENUM_DIR)})
    for task in seeds:
        registry.enqueue(task)
    logger.info("coordinator seeded %d initial task(s)", len(seeds))

    interrupted = False
    try:
        _drain(ctx, registry, monitor, sinks, progress, logger)
        logger.info("coordinator: all tasks accounted for")
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("scan interrupted by user (KeyboardInterrupt)")
        progress.log_event(
            "WARNING",
            "Scan interrupted — shutting down gracefully  (CTRL-C again to force-quit)",
        )
    finally:
        # Before teardown, which is where the display prints its summary.
        progress.report_incomplete(
            timed_out=registry.count_abandoned("timed_out"),
            abandoned=registry.count_abandoned("crashed"),
            unfinished=len(registry),
            interrupted=interrupted,
        )
        _teardown(ctx, pool, listener, sinks, progress, logger, interrupted=interrupted)

    unfinished = len(registry)
    if unfinished and not interrupted:
        logger.error("coordinator exited with %d task(s) still outstanding", unfinished)
    return CoordinatorResult(interrupted=interrupted, unfinished=unfinished)


def _drain(
    ctx: WorkerContext,
    registry: TaskRegistry,
    monitor: HealthMonitor,
    sinks: list[Any],
    progress: ProgressDisplay,
    logger: logging.Logger,
) -> None:
    """Consume results until the registry is empty, sweeping on a fixed cadence.

    Whether a sweep is due is checked after every message, not only when the
    queue times out.  A busy scan delivers messages more often than once per
    interval.  When the sweep waited for an idle queue, a hung or crashed worker
    went undetected for as long as the scan stayed busy.
    """
    next_sweep = time.monotonic() + HEARTBEAT_CHECK_INTERVAL
    while registry:
        try:
            message: Any = ctx.result_queue.get(timeout=max(0.0, next_sweep - time.monotonic()))
        except queue.Empty:
            pass
        else:
            _handle_message(message, registry, sinks, progress, logger)
        if time.monotonic() >= next_sweep:
            _report_sweep(monitor.tick(), registry, progress, logger)
            next_sweep = time.monotonic() + HEARTBEAT_CHECK_INTERVAL


def _handle_message(
    message: Any,
    registry: TaskRegistry,
    sinks: list[Any],
    progress: ProgressDisplay,
    logger: logging.Logger,
) -> None:
    """Dispatch one result-queue message by type."""
    if isinstance(message, TaskStarted):
        if not registry.record_start(message.task_id, message.worker_pid):
            # Expected, not an error: with task ids reused across retries, a
            # redundant copy can start after the task has already retired.
            logger.debug("heartbeat for untracked task %s from pid %d; ignoring", message.task_id, message.worker_pid)
    elif isinstance(message, TaskResult):
        _handle_result(message, registry, sinks, progress, logger)
    else:
        logger.warning("coordinator received unexpected message type %s", type(message).__name__)


def _handle_result(
    result: TaskResult,
    registry: TaskRegistry,
    sinks: list[Any],
    progress: ProgressDisplay,
    logger: logging.Logger,
) -> None:
    """Retire the task a result reports on, then act on what the result contains.

    A result for an untracked id is usually a duplicate: another copy of a
    re-dispatched task finished first, and its findings are already written.
    Such a result is dropped whole.  The exception is a task the coordinator
    abandoned.  No other copy of it exists, so a late result is the only report
    of that work, and dropping it would lose real findings.
    """
    record = registry.retire(result.task_id)
    if record is None:
        record = registry.reclaim(result.task_id)
        if record is None:
            logger.debug("dropping duplicate result for task %s", result.task_id)
            return
        logger.warning(
            "[%s] accepting late result for %r after the coordinator gave up on it",
            result.task_type.value,
            _task_path(record.task),
        )
    task = record.task

    if result.status == "error":
        msg = result.error_message or "(no message)"
        err_path = _task_path(task)
        logger.error(
            "[%s]%s: %s",
            result.task_type.value,
            f" path={err_path!r}" if err_path else "",
            msg,
        )
        if _is_access_denied(msg):
            progress.log_event("WARNING", f"Access denied: {_truncate_path(_denied_path(msg))}")
        elif result.task_type == TaskType.SCAN_FILE:
            file_path = str(task.payload.get("display_path", ""))
            progress.log_event("ERROR", f"Error: {_truncate_path(file_path)} — {_short_error(msg)}")

    for new_task_dict in result.new_tasks:
        # Task has extra="forbid", so a malformed producer dict raises
        # ValidationError.  Unguarded that would escape the loop entirely and
        # abort the whole scan over one bad child task.
        try:
            child = Task(**new_task_dict)
        except ValidationError:
            logger.exception(
                "[%s] dropping malformed child task from %r: %r",
                result.task_type.value,
                _task_path(task),
                new_task_dict,
            )
            progress.log_event("ERROR", f"Malformed task from {_truncate_path(_task_path(task))}")
            continue
        registry.enqueue(child)

    _route_to_sinks(result.findings, sinks, logger)
    if result.findings:
        progress.log_event("INFO", _findings_summary(result.findings))
    # One update per result, so the display refreshes once.  tasks_pending is
    # read after the children above were enqueued.  A failed task counts as
    # completed for the ETA, and separately as not scanned for the summary.
    update = {**result.counters, "tasks_completed": 1, "tasks_pending": len(registry)}
    if result.status == "error":
        update["tasks_failed"] = 1
    progress.update(update)


def _report_sweep(
    sweep: SweepResult,
    registry: TaskRegistry,
    progress: ProgressDisplay,
    logger: logging.Logger,
) -> None:
    """Turn a SweepResult into log records, display events, and summary counters."""
    if not sweep:
        return

    for record in sweep.timed_out:
        task_type = record.task.task_type.value
        timeout = record.task.timeout_seconds
        elapsed = sweep.now - record.started_at if record.started_at is not None else 0.0
        logger.warning(
            "deadline exceeded: type=%s path=%r pid=%s elapsed=%.1fs timeout=%ds; stopping and replacing the worker",
            task_type,
            _task_path(record.task),
            record.worker_pid,
            elapsed,
            timeout,
        )
        progress.log_event(
            "WARNING",
            f"Timeout [{task_type}] {_label(record)} — pid={record.worker_pid}, {elapsed:.0f}s/{timeout}s",
        )

    for pid, exitcode in sweep.crashed:
        logger.warning("worker pid=%d died unexpectedly (exit code %s); replacing it", pid, exitcode)
        progress.log_event("WARNING", f"Worker pid={pid} crashed unexpectedly (exit code {exitcode})")

    for record in sweep.redispatched:
        logger.warning(
            "[%s] re-dispatching %r after its worker died (retry %d of %d)",
            record.task.task_type.value,
            _task_path(record.task),
            record.attempt,
            registry.max_retries,
        )

    for record in sweep.abandoned:
        attempts = record.attempt + 1
        logger.error(
            "[%s] abandoning %r: its worker died on all %d attempts",
            record.task.task_type.value,
            _task_path(record.task),
            attempts,
        )
        progress.log_event("ERROR", f"Gave up on {_label(record)} after its worker crashed {attempts} times")

    # Timeout and abandonment totals are not accumulated here.  A late result can
    # still reclaim an abandoned task, so the final counts come from the
    # registry when the run ends.
    progress.update({"tasks_pending": len(registry)})


def _label(record: TaskRecord) -> str:
    """Short display label for a task: its truncated path, or its id if it has none."""
    path = _task_path(record.task)
    return _truncate_path(path) if path else f"task {record.task_id[:8]}…"


def _route_to_sinks(findings: list[dict[str, Any]], sinks: list[Any], logger: logging.Logger) -> None:
    """Forward findings to each OutputSink.

    Findings cross the process boundary as plain dicts, which pickle.  Sinks
    expect validated ResultRecord objects, so they are reconstituted here at the
    coordinator boundary.
    """
    from piidigger.models.results import ResultRecord  # local: avoids a circular import at module level

    for finding_dict in findings:
        try:
            record = ResultRecord.model_validate(finding_dict)
        except Exception:  # noqa: BLE001
            logger.warning("coordinator: could not deserialize finding: %r", finding_dict)
            continue
        for sink in sinks:
            sink.write(record)


def _flush_sinks(sinks: list[Any], logger: logging.Logger) -> None:
    """Close every output sink, logging rather than raising on failure."""
    for sink in sinks:
        try:
            sink.close()
        except Exception:  # noqa: BLE001
            logger.exception("error closing sink %r", sink)


def _teardown(
    ctx: WorkerContext,
    pool: WorkerPool,
    listener: logging.handlers.QueueListener,
    sinks: list[Any],
    progress: ProgressDisplay,
    logger: logging.Logger,
    *,
    interrupted: bool,
) -> None:
    """Stop the workers, flush the sinks, and stop the listener and display."""
    if interrupted:
        # Cancel feeder-thread joins NOW, before any teardown step that could
        # block.  If a second CTRL-C breaks out of this function, the atexit
        # handler sees _joincancelled=True and skips thread.join(), so the
        # multiprocessing atexit hook raises no unhandled KeyboardInterrupt.
        ctx.task_queue.cancel_join_thread()
        ctx.result_queue.cancel_join_thread()
        ctx.log_queue.cancel_join_thread()
        pool.terminate_all()
    else:
        # One sentinel per process the pool knows about, stragglers included.
        # Too many is harmless; too few leaves a worker blocked in get() forever.
        broadcast_shutdown(ctx.task_queue, len(pool.processes))

    try:
        if interrupted:
            progress.log_event("INFO", "Waiting for workers to stop…")
        pool.join(_INTERRUPT_JOIN_TIMEOUT if interrupted else _JOIN_TIMEOUT)

        if interrupted:
            progress.log_event("INFO", "Saving results to output files…")
        _flush_sinks(sinks, logger)
        stop_listener(listener)

    except KeyboardInterrupt:
        # Second CTRL-C: force-quit without waiting for a clean teardown.
        pool.terminate_all()
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

    listener = start_listener(ctx.log_queue, Path(log_file_str), "DEBUG")
    pool = WorkerPool(lambda: spawn_worker(ctx), logger=build_worker_logger(ctx.log_queue, "pool"))
    pool.start(n_workers)
    progress = ProgressDisplay()
    progress._is_tty = False
    run_coordinator(ctx, pool, listener, [], progress)
