from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from piidigger.models.config import Config
from piidigger.models.tasks import Task, TaskProgress, TaskResult, TaskStarted, TaskType, WorkerReady
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger, start_listener, stop_listener
from piidigger.orchestration.pool import WorkerPool, spawn_worker
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.registry import AbandonReason, TaskRecord, TaskRegistry
from piidigger.orchestration.sinks import GuardedSink
from piidigger.orchestration.worker import broadcast_shutdown

# How often (seconds) the coordinator runs its health sweep.  The sweep is
# scheduled by elapsed time, not by the result queue going quiet, so a busy scan
# is checked on the same cadence as an idle one.
HEARTBEAT_CHECK_INTERVAL: float = 1.0

# Lost-task sweep: the conditions under which outstanding work is judged lost
# rather than slow must hold for this many consecutive sweeps, with no message
# arriving in between...
LOST_TASK_CONFIRM_SWEEPS: int = 2
# ...and every outstanding task must have been waiting at least this long.
# Together these cover the moment between a worker taking a task and its
# heartbeat reaching the coordinator.
LOST_TASK_MIN_AGE: float = 2.0

# How long teardown waits for workers to exit before stopping them.
_JOIN_TIMEOUT: float = 5.0
_INTERRUPT_JOIN_TIMEOUT: float = 2.0

_ACCESS_DENIED_PHRASES: tuple[str, ...] = ("Access is denied", "Permission denied", "WinError 5", "Errno 13")


@dataclass(frozen=True)
class CoordinatorResult:
    """How the scan ended, so the caller can pick an exit code.

    The coordinator deliberately does not choose exit codes itself — that is a
    CLI concern, and run_scan owns the mapping.

    A dataclass rather than a Pydantic model: every field is computed by
    run_coordinator from its own local state and read by run_scan in the same
    process, so there is no external input to validate and nothing to serialise.

    unfinished > 0 means the loop exited with work still outstanding.  On a clean
    run that is impossible; it is reported rather than swallowed so a truncated
    scan cannot masquerade as a successful one.

    workers_failed means the pool stopped replacing workers because they kept
    dying before checking in, and no worker was left.  The run still ends cleanly, because the lost-task
    sweep abandons the stranded work, but it did not really scan anything.  Unlike
    a failure on one file, this is a failure of the whole run.

    sinks_failed names each results file that stopped receiving findings after
    an I/O error.  The scan itself may have finished, but its results did not
    all reach disk.
    """

    interrupted: bool = False
    unfinished: int = 0
    workers_failed: bool = False
    sinks_failed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkippedItem:
    """One member dropped from a batch because it hung or crashed its worker.

    A dataclass rather than a Pydantic model: every field comes from our own
    registry and pool.  worker_pid is captured separately because re-queuing
    the batch clears it on the record.
    """

    record: TaskRecord
    item: str
    reason: AbandonReason
    worker_pid: int | None


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
    lost: list[TaskRecord] = field(default_factory=list)
    abandoned: list[TaskRecord] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    stopped_replacing: bool = False

    def __bool__(self) -> bool:
        """True when the sweep has anything to report."""
        return bool(
            self.timed_out
            or self.crashed
            or self.redispatched
            or self.lost
            or self.abandoned
            or self.skipped
            or self.stopped_replacing
        )


class HealthMonitor:
    """The coordinator's periodic health sweep.

    Kept apart from the drain loop, with its collaborators injected, so it can be
    unit-tested without spawning a process.  tick() runs three checks, and the
    order matters:

    1. Deadline sweep.  A RUNNING task past its deadline is abandoned and its
       worker replaced.  Timeouts are not retried: a task that hung once will
       most likely hang again.
    2. Crash sweep.  Workers that died unprompted are replaced.  Then every
       RUNNING task whose worker is no longer in the pool is re-dispatched, or
       abandoned once its retry budget is spent.  Matching on "not in the pool"
       rather than "died this tick" also catches a heartbeat that arrived after
       its worker had already been reaped.

    3. Lost-task sweep.  A task lost before its heartbeat is invisible to the
       first two checks, because no worker is recorded as holding it.  It is
       found instead by elimination.  A worker that has checked in and is not
       running a task can only be waiting in task_queue.get(), and it would
       take a queued task at once.  So if nothing is running, every live worker
       has checked in, and that stays true across LOST_TASK_CONFIRM_SWEEPS
       sweeps with no message in between, whatever is still outstanding is not
       in the queue and not held by anyone: it is lost, not slow.  It is
       re-dispatched, or abandoned once its retry budget is spent.  No queue
       introspection is involved: qsize() is unimplemented on macOS and
       empty() is only approximate.

    Because the deadline sweep runs first, a worker replaced for a timeout has
    already left the pool, so the crash sweep cannot replace it a second time.

    Archive batches (tasks with items) get one refinement in steps 1 and 2.
    When the batch's worker had said which member it was working on, only that
    member is dropped, and the rest of the batch is re-queued.  That uses no
    retry budget, because every such re-queue removes a member.  Without a
    member in progress, a batch follows the same rules as any other task.

    A false positive in step 3 is harmless.  A task re-dispatched while its
    original was still pending keeps its task_id, so whichever copy finishes
    second is dropped as a duplicate.
    """

    def __init__(
        self,
        registry: TaskRegistry,
        pool: WorkerPool,
        *,
        clock: Callable[[], float] = time.monotonic,
        lost_task_confirm_sweeps: int = LOST_TASK_CONFIRM_SWEEPS,
        lost_task_min_age: float = LOST_TASK_MIN_AGE,
    ) -> None:
        self._registry = registry
        self._pool = pool
        self._clock = clock
        self._lost_task_confirm_sweeps = lost_task_confirm_sweeps
        self._lost_task_min_age = lost_task_min_age
        self._quiet_sweeps = 0

    def observe(self, message: object) -> None:
        """Note one result-queue message.  Call for every message received.

        Any message at all means the system is not quiet, so the lost-task
        sweep's quiet count restarts.  WorkerReady and TaskStarted also check
        the sending worker in with the pool.
        """
        self._quiet_sweeps = 0
        if isinstance(message, WorkerReady | TaskStarted):
            self._pool.check_in(message.worker_pid)

    def tick(self) -> SweepResult:
        """Run one sweep and report what it did."""
        now = self._clock()

        timed_out: list[TaskRecord] = []
        skipped: list[SkippedItem] = []
        for record in self._registry.expired(now):
            pid = record.worker_pid
            culprit = self._registry.requeue_remaining(record.task_id, reason="timed_out")
            if culprit is not None:
                skipped.append(SkippedItem(record, culprit, "timed_out", pid))
            else:
                self._registry.abandon(record.task_id, reason="timed_out")
                timed_out.append(record)
            if pid is not None:
                self._pool.replace(pid)

        was_replacing = self._pool.replacing
        crashed = self._pool.reap_dead()
        stopped_replacing = was_replacing and not self._pool.replacing
        live = self._pool.pids()
        redispatched: list[TaskRecord] = []
        abandoned: list[TaskRecord] = []
        for record in self._registry.running():
            if record.worker_pid in live:
                continue
            pid = record.worker_pid
            culprit = self._registry.requeue_remaining(record.task_id, reason="crashed")
            if culprit is not None:
                skipped.append(SkippedItem(record, culprit, "crashed", pid))
            elif self._registry.redispatch(record.task_id) is not None:
                redispatched.append(record)
            else:
                self._registry.abandon(record.task_id, reason="crashed")
                abandoned.append(record)

        lost: list[TaskRecord] = []
        if timed_out or crashed or redispatched or abandoned or skipped:
            # Something just changed, so quiet has to be observed afresh.
            self._quiet_sweeps = 0
        elif self._work_looks_lost(now):
            self._quiet_sweeps += 1
            if self._quiet_sweeps >= self._lost_task_confirm_sweeps:
                self._quiet_sweeps = 0
                for record in self._registry.records():
                    if self._registry.redispatch(record.task_id) is not None:
                        lost.append(record)
                    else:
                        self._registry.abandon(record.task_id, reason="crashed")
                        abandoned.append(record)
        else:
            self._quiet_sweeps = 0

        return SweepResult(
            now=now,
            timed_out=timed_out,
            crashed=crashed,
            redispatched=redispatched,
            lost=lost,
            abandoned=abandoned,
            skipped=skipped,
            stopped_replacing=stopped_replacing,
        )

    def _work_looks_lost(self, now: float) -> bool:
        """True when no process could be holding any outstanding task."""
        if not self._registry or self._registry.any_running() or not self._pool.all_checked_in():
            return False
        return all(now - r.enqueued_at >= self._lost_task_min_age for r in self._registry.records())


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
    if task.task_type in (TaskType.ENUM_ARCHIVE_MEMBERS, TaskType.SCAN_ARCHIVE_MEMBERS):
        return str(p.get("archive_path", ""))
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
            Each is wrapped in a GuardedSink, so a failing sink is logged and
            dropped instead of stopping the scan.
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
    guarded = [GuardedSink(sink, logger, progress) for sink in sinks]

    seeds = build_seed_tasks(ctx.config) if seed_tasks is None else list(seed_tasks)
    # Pre-seed dirs_found so the progress bar starts at "0 / N" rather than
    # "0 / 0".  Each ENUM_DIR result adds the subdirectories it discovers.
    progress.update({"dirs_found": sum(1 for t in seeds if t.task_type == TaskType.ENUM_DIR)})
    for task in seeds:
        registry.enqueue(task)
    logger.info("coordinator seeded %d initial task(s)", len(seeds))

    interrupted = False
    try:
        _drain(ctx, registry, monitor, guarded, progress, logger)
        logger.info("coordinator: all tasks accounted for")
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("scan interrupted by user (KeyboardInterrupt)")
        progress.log_event(
            "WARNING",
            "Scan interrupted — shutting down gracefully  (CTRL-C again to force-quit)",
        )
    finally:
        # Checked before teardown, whose join() can drop stuck workers.  The run
        # failed as a whole only if the pool stopped replacing workers AND none
        # were left.  A breaker trip that left healthy workers still finishes
        # the scan, just with less capacity.
        workers_failed = not pool.replacing and pool.size == 0
        # Before teardown, which is where the display prints its summary.
        progress.report_incomplete(
            timed_out=registry.count_abandoned("timed_out"),
            abandoned=registry.count_abandoned("crashed"),
            unfinished=registry.count_unfinished(),
            interrupted=interrupted,
            workers_failed=workers_failed,
        )
        _teardown(ctx, pool, listener, guarded, progress, logger, interrupted=interrupted)

    unfinished = len(registry)
    if unfinished and not interrupted:
        logger.error("coordinator exited with %d task(s) still outstanding", unfinished)
    return CoordinatorResult(
        interrupted=interrupted,
        unfinished=unfinished,
        workers_failed=workers_failed,
        # After teardown, so a sink that fails while closing is counted too.
        sinks_failed=tuple(sink.label for sink in guarded if sink.failed),
    )


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
            monitor.observe(message)
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
    if isinstance(message, WorkerReady):
        return  # pool bookkeeping only; HealthMonitor.observe() has recorded it
    if isinstance(message, TaskStarted):
        if not registry.record_start(message.task_id, message.worker_pid):
            # Expected, not an error: with task ids reused across retries, a
            # redundant copy can start after the task has already retired.
            logger.debug("heartbeat for untracked task %s from pid %d; ignoring", message.task_id, message.worker_pid)
    elif isinstance(message, TaskProgress):
        _handle_progress(message, registry, sinks, progress, logger)
    elif isinstance(message, TaskResult):
        _handle_result(message, registry, sinks, progress, logger)
    else:
        logger.warning("coordinator received unexpected message type %s", type(message).__name__)


def _handle_progress(
    message: TaskProgress,
    registry: TaskRegistry,
    sinks: list[Any],
    progress: ProgressDisplay,
    logger: logging.Logger,
) -> None:
    """Apply a TaskProgress: push the deadline back, and write a finished item's findings.

    An item_done the registry does not accept is a duplicate — another copy of
    the batch already reported that item — so its findings and counters are
    dropped, exactly as a duplicate TaskResult is.
    """
    accepted = registry.record_progress(message.task_id, message.worker_pid, message.event, message.item)
    if message.event != "item_done":
        return
    if not accepted:
        logger.debug("dropping duplicate progress for %r in task %s", message.item, message.task_id)
        return
    _route_to_sinks(message.findings, sinks, logger)
    if message.findings:
        progress.log_event("INFO", _findings_summary(message.findings))
    if message.counters:
        progress.update(message.counters)


def _requeue_failed_batch(
    result: TaskResult,
    registry: TaskRegistry,
    progress: ProgressDisplay,
    logger: logging.Logger,
) -> bool:
    """Keep a batch going when it failed on one member.

    A batch reports an error result when its archive could not be read, which
    happens while it is working on some member.  If the worker had said which
    member that was, only that member is counted as failed, and the rest of the
    batch is re-queued, as after a crash.  Each re-queue removes a member, so a
    damaged archive cannot loop.  If the archive is unreadable from that point
    on, the re-queued batch fails again before starting any member, and its
    members are then counted as failed by the usual result handling.

    Returns True when the batch was re-queued (or finished) here; False when the
    result needs the usual handling.
    """
    record = registry.get(result.task_id)
    if record is None or not record.task.items or record.worker_pid != result.worker_pid:
        return False
    path = _task_path(record.task)
    culprit = registry.requeue_remaining(result.task_id, reason=None)
    if culprit is None:
        return False
    member = f"{path}::{culprit}"
    msg = result.error_message or "(no message)"
    logger.error(
        "[%s] %r failed: %s; re-queuing the %d member(s) left in the batch",
        result.task_type.value,
        member,
        msg,
        len(record.remaining),
    )
    progress.log_event("ERROR", f"Error: {_truncate_path(member)} — {_short_error(msg)}")
    update = {"files_scanned": 1, "tasks_failed": 1, "tasks_pending": len(registry)}
    if result.task_id not in registry:
        update["tasks_completed"] = 1  # the failed member was the batch's last
    progress.update(update)
    return True


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
    if result.status == "error" and _requeue_failed_batch(result, registry, progress, logger):
        return
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
    # A batch reports each member through TaskProgress, so by now its remaining
    # set is normally empty.  Whatever is left was never reported.  On an error
    # that is expected (the archive broke partway); on success it is a bug, and
    # either way those members were not scanned.
    unreported = len(record.remaining) if task.items else 0
    if unreported and result.status != "error":
        logger.warning(
            "[%s] %r finished without reporting %d member(s)", result.task_type.value, _task_path(task), unreported
        )
    # One update per result, so the display refreshes once.  tasks_pending is
    # read after the children above were enqueued.  A failed task counts as
    # completed for the ETA, and separately as not scanned for the summary.
    update = {**result.counters, "tasks_completed": 1, "tasks_pending": len(registry)}
    if result.status == "error" and not task.items:
        update["tasks_failed"] = 1
    elif unreported:
        update["tasks_failed"] = unreported
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

    for skip in sweep.skipped:
        what = "hung" if skip.reason == "timed_out" else "crashed"
        member = f"{_task_path(skip.record.task)}::{skip.item}"
        logger.warning(
            "[%s] skipping %r: its worker (pid=%s) %s on it; re-queuing the %d member(s) left in the batch",
            skip.record.task.task_type.value,
            member,
            skip.worker_pid,
            what,
            len(skip.record.remaining),
        )
        progress.log_event("WARNING", f"Skipped {_truncate_path(member)} — worker {what}")

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

    for record in sweep.lost:
        logger.warning(
            "[%s] re-dispatching %r: no worker holds it, so it was lost before starting (retry %d of %d)",
            record.task.task_type.value,
            _task_path(record.task),
            record.attempt,
            registry.max_retries,
        )

    if sweep.stopped_replacing:
        progress.log_event("ERROR", "Workers are failing to start — continuing without replacements. See the log.")

    for record in sweep.abandoned:
        attempts = record.attempt + 1
        logger.error(
            "[%s] abandoning %r: lost or crashed on all %d attempts",
            record.task.task_type.value,
            _task_path(record.task),
            attempts,
        )
        progress.log_event("ERROR", f"Gave up on {_label(record)} after {attempts} failed attempts")

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
    """Stop the workers, flush the sinks, and stop the listener and display.

    Normal completion and CTRL-C take the same path; only the join budget
    differs.  Workers are asked to stop, not killed outright.  Killing a worker
    can cut a queue message in half, and a half-written message can hang
    whoever reads it next.  pool.join() still escalates to terminate() and
    kill() for any worker that does not exit within the budget.
    """
    if interrupted:
        # Cancel feeder-thread joins NOW, before any teardown step that could
        # block.  If a second CTRL-C breaks out of this function, the atexit
        # handler sees _joincancelled=True and skips thread.join(), so the
        # multiprocessing atexit hook raises no unhandled KeyboardInterrupt.
        ctx.task_queue.cancel_join_thread()
        ctx.result_queue.cancel_join_thread()
        ctx.log_queue.cancel_join_thread()

    # Set before the sentinels go out.  Sentinels queue up behind anything
    # still in the task queue; with stop_event set, a worker that takes one of
    # those leftovers exits instead of running it.
    ctx.stop_event.set()
    # One sentinel per process the pool knows about, stragglers included, so
    # every worker blocked in get() wakes.  Too many is harmless; too few leaves
    # a worker blocked forever.
    broadcast_shutdown(ctx.task_queue, len(pool.processes))

    listener_stopped = True
    try:
        if interrupted:
            progress.log_event("INFO", "Waiting for workers to stop…")
        pool.join(_INTERRUPT_JOIN_TIMEOUT if interrupted else _JOIN_TIMEOUT)

        # Every worker has exited or been stopped, so nothing will read the task
        # queue again.  Anything still buffered in it (re-dispatched copies, or
        # every task if no worker was left) would otherwise make interpreter
        # exit wait on a pipe that no one drains.
        ctx.task_queue.cancel_join_thread()

        if interrupted:
            progress.log_event("INFO", "Saving results to output files…")
        _flush_sinks(sinks, logger)
        listener_stopped = stop_listener(listener)

    except KeyboardInterrupt:
        # Second CTRL-C: force-quit without waiting for a clean teardown.
        pool.terminate_all()
        ctx.task_queue.cancel_join_thread()
        ctx.result_queue.cancel_join_thread()
        ctx.log_queue.cancel_join_thread()
        progress.log_event("WARNING", "Force-quit — remaining output abandoned")

    finally:
        progress.stop()
        if not listener_stopped:
            # Too late for the log itself, whose listener is what failed.
            print("warning: the log listener did not stop in time; the log file may be incomplete", file=sys.stderr)  # noqa: T201


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
