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
from dataclasses import dataclass

from piidigger.models.tasks import Task

# Maximum re-dispatch attempts before a task is abandoned.  The budget is what
# stops a poison task — a malformed file that segfaults a C parser, killing its
# worker outright — from looping forever, re-crashing each replacement.
MAX_RETRIES: int = 3

# A task's wall-clock deadline is this multiple of its declared timeout.  The
# slack absorbs queue latency and scheduling jitter, so only a genuinely hung
# task trips it.
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
    """

    task: Task
    enqueued_at: float
    attempt: int = 0
    worker_pid: int | None = None
    started_at: float | None = None

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
        lost before its heartbeat — quiescence detection covers that case.
        """
        if self.started_at is None:
            return None
        return self.started_at + _DEADLINE_FACTOR * self.task.timeout_seconds


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
        record = TaskRecord(task=task, enqueued_at=self._clock(), attempt=attempt)
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
        """
        record = self._records.get(task_id)
        if record is None or record.attempt >= self._max_retries:
            return None
        record.attempt += 1
        record.worker_pid = None
        record.started_at = None
        record.enqueued_at = self._clock()
        self._running.pop(task_id, None)
        self._put(record.task)
        return record

    # -- observation --------------------------------------------------------

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def records(self) -> list[TaskRecord]:
        """Every outstanding record, QUEUED and RUNNING alike."""
        return list(self._records.values())

    def any_running(self) -> bool:
        """True while at least one task has an unanswered heartbeat."""
        return bool(self._running)

    def running_for_pid(self, pid: int) -> list[TaskRecord]:
        """Tasks currently RUNNING on a given worker, for crash recovery."""
        return [r for r in self._running.values() if r.worker_pid == pid]

    def expired(self, now: float | None = None) -> list[TaskRecord]:
        """RUNNING tasks past their deadline.  QUEUED tasks can never appear."""
        moment = self._clock() if now is None else now
        return [r for r in self._running.values() if r.deadline is not None and moment > r.deadline]
