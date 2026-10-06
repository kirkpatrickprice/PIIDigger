"""The coordinator's record of outstanding work.

TaskRegistry is the single authoritative answer to "is there any work left?".
Its length IS the outstanding-task count — there is no separate counter that
could drift away from the set it is supposed to describe.

A task is *registered* when it is enqueued and *retired* when its outcome has
been accounted for.  Absence from the registry is what "retired" means, so the
drain loop condition is simply `while registry:`.

This module deliberately knows nothing about multiprocessing.  The task queue is
reached only through the `put` callable handed to __init__, which makes the whole
module unit-testable without spawning a process.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from piidigger.models.tasks import ProgressEvent, Task

# Maximum re-dispatch attempts before a task is abandoned.  The budget is what
# stops a poison task — a malformed file that segfaults a C parser, killing its
# worker outright — from looping forever, re-crashing each replacement.
MAX_RETRIES: int = 3

# Why the coordinator stopped waiting for a task.  "timed_out": it ran past its
# deadline.  "crashed": its worker died on every attempt the retry budget allowed.
type AbandonReason = Literal["timed_out", "crashed"]

# A task's wall-clock deadline is this multiple of its declared timeout, counted
# from its start or from its last TaskProgress, whichever is later.  The slack
# absorbs queue latency and scheduling jitter, so only a genuinely hung task
# trips it.
_DEADLINE_FACTOR: int = 2


@dataclass(slots=True)
class TaskRecord:
    """One outstanding task plus the bookkeeping the coordinator needs for it.

    A plain dataclass rather than a Pydantic model, for three reasons:

    * Nothing here needs validating.  `task` is an already-validated model,
      `enqueued_at`/`started_at` come from our own monotonic clock, `attempt` is
      our own counter, and `worker_pid` is an OS-reported pid relayed by our own
      TaskStarted.  No field originates outside this codebase.
    * It is mutated in place on every heartbeat and every re-dispatch.  Pydantic
      does not validate on assignment by default, so a model would add
      __setattr__ overhead on the hot path and buy nothing for it.
    * There is one record per outstanding task, and a large scan leaves six
      figures of them.  Measured at 100k records: slots 8.0 MB, plain dataclass
      12.0 MB, Pydantic model 50.4 MB.

    slots=True therefore earns its place; the class needs no dynamic attributes
    and no weakref support.  Revisit all of this only if a record ever has to be
    serialised — persisting a scan for resume, say — since that is the one job a
    model would do better.  A record never crosses the process boundary today:
    enqueue() puts the Task on the queue, not the record.

    started_at is the state marker: None means QUEUED, set means RUNNING.

    The last three fields matter only for a task with items (an archive
    batch).  remaining is the single record of the items still to do.  It is
    a dict used as an ordered set: it keeps archive order and removes in O(1),
    where removing from the front of a list would itself be quadratic over a
    10,000-member batch.  current_item is the item the holding worker said it
    was starting and has not yet finished — the one to blame if the worker
    hangs or dies.
    """

    task: Task
    enqueued_at: float
    attempt: int = 0
    worker_pid: int | None = None
    started_at: float | None = None
    last_progress_at: float | None = None
    current_item: str | None = None
    remaining: dict[str, None] = field(default_factory=dict)

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def is_running(self) -> bool:
        """True once a TaskStarted heartbeat has arrived for the current attempt."""
        return self.started_at is not None

    @property
    def deadline(self) -> float | None:
        """Monotonic time after which this task counts as hung, or None if QUEUED.

        A queued task has no deadline because no worker has claimed it yet.
        That is precisely why the deadline sweep alone cannot recover a task
        lost before its heartbeat — the lost-task sweep covers that case.

        Progress from the holding worker moves the deadline forward, so a long
        batch that keeps finishing members never times out, while one that goes
        quiet times out exactly as a single-file task does.
        """
        if self.started_at is None:
            return None
        last_sign_of_life = max(self.started_at, self.last_progress_at or self.started_at)
        return last_sign_of_life + _DEADLINE_FACTOR * self.task.timeout_seconds

    @property
    def units(self) -> int:
        """How many files or members this record stands for, for the end-of-scan summary."""
        return len(self.remaining) if self.task.items else 1


class TaskRegistry:
    """Every task enqueued and not yet accounted for.

    Two invariants carry the design:

    * enqueue() is the ONLY place a Task reaches the task queue.  Registering and
      putting happen in one body, so a queued task is always counted — there is
      no increment for a caller to forget.
    * retire() is the ONLY removal.  It is state-agnostic and idempotent: an
      unknown or already-retired id returns None and changes nothing.  That is
      what makes a late, duplicate, or superseded result harmless instead of a
      silent double-decrement.
    """

    def __init__(
        self,
        put: Callable[[Task], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        """Build a registry.

        Args:
            put: Places a Task on the task queue.  Injected so tests can pass a
                list's append and observe dispatch without multiprocessing.
            clock: Monotonic time source, injected so deadline tests need no sleep.
            max_retries: Re-dispatch budget per task.
        """
        self._put = put
        self._clock = clock
        self._max_retries = max_retries
        self._records: dict[str, TaskRecord] = {}
        # Derived index holding only RUNNING records, so it is bounded by the
        # worker count rather than by outstanding work.  Not a second source of
        # truth: it is maintained solely by the mutators below and __len__ never
        # consults it.  It exists so the once-per-second deadline sweep stays
        # O(workers) instead of O(outstanding), which matters when a large scan
        # leaves six figures of tasks queued.
        self._running: dict[str, TaskRecord] = {}
        # Records the coordinator gave up on, kept so a late result can still be
        # accepted.  See abandon().  Only timeouts and exhausted retry budgets land
        # here, so it stays small.
        self._abandoned: dict[str, tuple[TaskRecord, AbandonReason]] = {}
        # Items dropped from a batch because they hung or crashed their worker,
        # keyed by (task_id, item).  Kept apart from the records because the
        # batch carries on and is eventually retired, but the skipped item still
        # belongs in the end-of-scan summary.  See requeue_remaining().
        self._skipped: dict[tuple[str, str], AbandonReason] = {}

    # -- size / termination -------------------------------------------------

    def __len__(self) -> int:
        """Number of tasks still outstanding.  This IS the pending count."""
        return len(self._records)

    def __bool__(self) -> bool:
        """True while work remains, so the drain loop reads `while registry:`."""
        return bool(self._records)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._records

    @property
    def max_retries(self) -> int:
        return self._max_retries

    # -- mutation -----------------------------------------------------------

    def enqueue(self, task: Task, *, attempt: int = 0) -> TaskRecord:
        """Register a task and place it on the task queue.

        The record is inserted *before* the put.  A worker can dequeue and send
        its heartbeat almost immediately, and that heartbeat must not arrive for
        a task the registry has not heard of yet.
        """
        record = TaskRecord(
            task=task,
            enqueued_at=self._clock(),
            attempt=attempt,
            remaining=dict.fromkeys(task.items),
        )
        self._records[task.task_id] = record
        self._put(task)
        return record

    def retire(self, task_id: str) -> TaskRecord | None:
        """Account for a task and remove it.  The only way out of the registry.

        Returns the retired record, or None when the id is not tracked — an
        already-retired task, or one that never existed.  Callers rely on that
        None to drop stale results without touching the outstanding count.

        Works on a QUEUED record as well as a RUNNING one.  Do not add a
        "must be running" assertion: a result can legitimately arrive for a task
        that has since been re-dispatched and is waiting to be claimed again.
        """
        record = self._records.pop(task_id, None)
        if record is not None:
            self._running.pop(task_id, None)
        return record

    def record_start(self, task_id: str, worker_pid: int) -> bool:
        """Mark a task RUNNING in response to a TaskStarted heartbeat.

        Returns False when the id is not tracked, so the caller can log it
        rather than resurrect a retired task.  Idempotent for a task already
        running: the pid and clock are simply overwritten, which is correct when
        a re-dispatched copy is picked up by a different worker.
        """
        record = self._records.get(task_id)
        if record is None:
            return False
        record.worker_pid = worker_pid
        record.started_at = self._clock()
        self._running[task_id] = record
        return True

    def redispatch(self, task_id: str) -> TaskRecord | None:
        """Queue a tracked task for another attempt under the *same* task_id.

        Returns the updated record, or None when the id is untracked or its
        retry budget is spent.  Reusing the id is deliberate: whichever copy
        finishes first retires the task, and any later duplicate finds the id
        gone and is dropped, so findings cannot be written twice.

        A budget-exhausted task is left in the registry.  The caller decides how
        to report giving up and then retires it, so "never heard of it" stays
        distinguishable from "tried and gave up".

        A batch goes back on the queue carrying only the items not yet done.
        """
        record = self._records.get(task_id)
        if record is None or record.attempt >= self._max_retries:
            return None
        record.attempt += 1
        self._requeue(record)
        return record

    def requeue_remaining(self, task_id: str, reason: AbandonReason | None) -> str | None:
        """Drop the item a batch was working on when it failed; re-queue the rest.

        reason says how the batch failed.  "timed_out" and "crashed" come from
        the health sweeps: the worker is gone, so the registry remembers the
        dropped item for the end-of-scan summary.  None means the worker itself
        reported an error result while on that item; the caller counts the item
        as failed, and the registry keeps no note of it.

        Returns the dropped item, or None when the task is untracked or no item
        was in progress — then there is nothing to blame, and the caller falls
        back to its usual handling.

        Uses no retry budget.  Each call removes one item, so a batch with a
        string of bad members still runs out of items and ends.  When the
        dropped item was the last one, nothing is re-queued: after a sweep the
        task is abandoned, which keeps the record so a late item_done for an
        item the worker did finish is still accepted; after an error result,
        the worker has finished, so the task is simply retired.

        After a sweep, if the dropped item's item_done arrives after all (it was
        already on the queue when the sweep ran), record_progress() accepts it
        and forgets the skip.
        """
        record = self._records.get(task_id)
        if record is None or record.current_item is None:
            return None
        culprit = record.current_item
        record.current_item = None
        if culprit in record.remaining:
            del record.remaining[culprit]
            if reason is not None:
                self._skipped[(task_id, culprit)] = reason
        if record.remaining:
            self._requeue(record)
        elif reason is None:
            self.retire(task_id)
        else:
            self.abandon(task_id, reason=reason)
        return culprit

    def _requeue(self, record: TaskRecord) -> None:
        """Reset a record to QUEUED and put its task back, trimmed to what remains."""
        if record.task.items:
            record.task = record.task.model_copy(update={"items": tuple(record.remaining)})
        record.worker_pid = None
        record.started_at = None
        record.last_progress_at = None
        record.current_item = None
        record.enqueued_at = self._clock()
        self._running.pop(record.task_id, None)
        self._put(record.task)

    def record_progress(self, task_id: str, worker_pid: int, event: ProgressEvent, item: str | None) -> bool:
        """Apply one TaskProgress message.  Returns whether to accept its findings.

        Any event from the worker holding the task pushes the deadline back.  A
        message from any other pid — a copy superseded by a re-dispatch — never
        does, so a stale worker cannot keep a task alive.

        For item_done, True means the item was outstanding and is now done; the
        caller writes its findings.  False means another copy already reported
        it, and its findings must be dropped so nothing is written twice.  The
        same holds for an abandoned task and for an item a sweep skipped: their
        work is real, so a late item_done for them is accepted once.

        For item_started and alive the return value only says whether the task
        is known.
        """
        record = self._records.get(task_id)
        if record is None:
            entry = self._abandoned.get(task_id)
            record = entry[0] if entry is not None else None
            holder = False
        else:
            holder = record.started_at is not None and record.worker_pid == worker_pid
        if holder and record is not None:
            record.last_progress_at = self._clock()

        if event == "item_started":
            if holder and record is not None:
                record.current_item = item
            return record is not None
        if event == "alive":
            return record is not None

        # item_done
        if item is None:
            return False
        if record is not None and holder and record.current_item == item:
            record.current_item = None
        if record is not None and item in record.remaining:
            del record.remaining[item]
            return True
        return self._skipped.pop((task_id, item), None) is not None

    def abandon(self, task_id: str, *, reason: AbandonReason) -> TaskRecord | None:
        """Retire a task the coordinator has stopped waiting for, keeping its record.

        Used when a task times out and when its retry budget runs out.  Neither
        leaves a replacement copy queued.  So if the abandoned attempt's result
        does turn up later, it is the only report of that work, and dropping it
        would silently lose real findings.  reclaim() lets the caller accept it.

        Returns the record, or None when the id is not tracked.
        """
        record = self.retire(task_id)
        if record is not None:
            self._abandoned[task_id] = (record, reason)
        return record

    def reclaim(self, task_id: str) -> TaskRecord | None:
        """Hand back an abandoned task's record, once, when its late result arrives.

        Returns None for any id that was not abandoned.  A late result for a task
        that was re-dispatched is a duplicate of the retry, not a lost report,
        and must still be dropped.  Does not change len(): an abandoned task was
        already retired.
        """
        entry = self._abandoned.pop(task_id, None)
        return entry[0] if entry is not None else None

    def count_abandoned(self, reason: AbandonReason) -> int:
        """Files or members given up on for this reason whose work never turned up.

        Counts units, not tasks: an abandoned batch counts the members it had
        not finished, and every member skipped out of a batch counts once.  A
        task reclaimed by a late result, or an item whose late item_done
        arrived, is no longer counted.  So at the end of a run this is exactly
        the work that did not complete.
        """
        abandoned = sum(record.units for record, why in self._abandoned.values() if why == reason)
        skipped = sum(1 for why in self._skipped.values() if why == reason)
        return abandoned + skipped

    def count_unfinished(self) -> int:
        """Files or members still outstanding.  Like len(), but a batch counts its remaining members."""
        return sum(record.units for record in self._records.values())

    # -- observation --------------------------------------------------------

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def records(self) -> list[TaskRecord]:
        """Every outstanding record, QUEUED and RUNNING alike."""
        return list(self._records.values())

    def any_running(self) -> bool:
        """True while at least one task has an unanswered heartbeat."""
        return bool(self._running)

    def running(self) -> list[TaskRecord]:
        """Every RUNNING record.  Bounded by the worker count, so cheap to scan."""
        return list(self._running.values())

    def expired(self, now: float | None = None) -> list[TaskRecord]:
        """RUNNING tasks past their deadline.  QUEUED tasks can never appear."""
        moment = self._clock() if now is None else now
        return [r for r in self._running.values() if r.deadline is not None and moment > r.deadline]
