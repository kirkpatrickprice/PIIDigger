"""Unit tests for TaskRegistry — the coordinator's outstanding-work record.

Every test here runs in-process with an injected `put` and clock, so the whole
lifecycle is exercised without spawning a worker or sleeping.  That is the point
of the class: the accounting that used to live in a closure inside
run_coordinator, untestable except by reimplementing it, is now directly
assertable.
"""

from __future__ import annotations

import pytest

from piidigger.models.tasks import Task, TaskType
from piidigger.orchestration.registry import MAX_RETRIES, TaskRecord, TaskRegistry


class FakeClock:
    """Monotonic clock under test control, so deadlines need no real waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _task(timeout: int = 30) -> Task:
    return Task(task_type=TaskType.NOOP, timeout_seconds=timeout)


def _registry(clock: FakeClock | None = None, max_retries: int = MAX_RETRIES) -> tuple[TaskRegistry, list[Task]]:
    """Registry wired to a list so dispatched tasks are directly observable."""
    dispatched: list[Task] = []
    reg = TaskRegistry(
        dispatched.append,
        clock=clock or FakeClock(),
        max_retries=max_retries,
    )
    return reg, dispatched


# ---------------------------------------------------------------------------
# enqueue / len — the outstanding count
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_new_registry_is_empty_and_falsy() -> None:
    reg, _ = _registry()
    assert len(reg) == 0
    assert not reg


@pytest.mark.unit
def test_enqueue_dispatches_and_counts() -> None:
    """One enqueue puts exactly one task on the queue and grows len by one."""
    reg, dispatched = _registry()
    task = _task()

    record = reg.enqueue(task)

    assert len(reg) == 1
    assert reg
    assert dispatched == [task]
    assert record.task_id == task.task_id
    assert record.attempt == 0
    assert not record.is_running


@pytest.mark.unit
def test_enqueue_registers_before_dispatching() -> None:
    """The record must exist before the task is visible to any worker.

    A worker can dequeue and heartbeat almost immediately; if the put happened
    first, that heartbeat could arrive for a task the registry has not yet
    recorded.  Asserted by checking membership from inside the put callable.
    """
    seen_by_put: list[bool] = []
    reg = TaskRegistry(lambda t: seen_by_put.append(t.task_id in reg))

    task = _task()
    reg.enqueue(task)

    assert seen_by_put == [True], "task reached the queue before it was registered"


@pytest.mark.unit
def test_enqueue_many_counts_each() -> None:
    reg, dispatched = _registry()
    for _ in range(25):
        reg.enqueue(_task())
    assert len(reg) == 25
    assert len(dispatched) == 25


# ---------------------------------------------------------------------------
# retire — the single removal point (defect 1 guard)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retire_returns_record_and_shrinks() -> None:
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)

    retired = reg.retire(task.task_id)

    assert retired is not None
    assert retired.task_id == task.task_id
    assert len(reg) == 0


@pytest.mark.unit
def test_retire_unknown_id_is_a_no_op() -> None:
    """The defect 1 guard: an untracked result must not change the count.

    The old coordinator popped defensively but decremented unconditionally, so a
    stale result silently undercounted pending and the loop could exit with work
    still outstanding.  There is no decrement here to get wrong.
    """
    reg, _ = _registry()
    reg.enqueue(_task())

    assert reg.retire("never-existed") is None
    assert len(reg) == 1


@pytest.mark.unit
def test_double_retire_is_a_no_op() -> None:
    """A duplicate result for an already-retired task changes nothing."""
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)

    assert reg.retire(task.task_id) is not None
    assert reg.retire(task.task_id) is None
    assert len(reg) == 0


@pytest.mark.unit
def test_retire_works_on_a_queued_task() -> None:
    """The T5 guard: a result can arrive for a task that is QUEUED, not RUNNING.

    Sequence: the task runs, its worker dies with the result already in flight,
    the sweep re-dispatches it back to QUEUED, and only then does the original
    result arrive.  retire() must not require the RUNNING state — adding such an
    assertion would discard a completed task's findings.
    """
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=4242)
    reg.redispatch(task.task_id)

    record = reg.get(task.task_id)
    assert record is not None
    assert not record.is_running, "redispatch should have returned the task to QUEUED"

    retired = reg.retire(task.task_id)

    assert retired is not None
    assert len(reg) == 0


# ---------------------------------------------------------------------------
# record_start — QUEUED -> RUNNING
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_record_start_marks_running() -> None:
    clock = FakeClock()
    reg, _ = _registry(clock)
    task = _task()
    reg.enqueue(task)
    clock.advance(5.0)

    assert reg.record_start(task.task_id, worker_pid=99) is True

    record = reg.get(task.task_id)
    assert record is not None
    assert record.is_running
    assert record.worker_pid == 99
    assert record.started_at == 1005.0
    assert reg.any_running()


@pytest.mark.unit
def test_record_start_unknown_id_creates_nothing() -> None:
    """A heartbeat for a retired task must not resurrect it."""
    reg, _ = _registry()

    assert reg.record_start("ghost", worker_pid=1) is False
    assert len(reg) == 0
    assert not reg.any_running()


@pytest.mark.unit
def test_record_start_is_idempotent_and_reassigns_pid() -> None:
    """A second heartbeat overwrites pid and clock — correct after re-dispatch."""
    clock = FakeClock()
    reg, _ = _registry(clock)
    task = _task()
    reg.enqueue(task)

    reg.record_start(task.task_id, worker_pid=1)
    clock.advance(10.0)
    reg.record_start(task.task_id, worker_pid=2)

    record = reg.get(task.task_id)
    assert record is not None
    assert record.worker_pid == 2
    assert record.started_at == 1010.0
    assert len(reg) == 1


@pytest.mark.unit
def test_any_running_false_until_heartbeat() -> None:
    reg, _ = _registry()
    reg.enqueue(_task())
    assert not reg.any_running()


# ---------------------------------------------------------------------------
# running_for_pid — the crash-recovery reverse index
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_running_for_pid_selects_only_that_worker() -> None:
    reg, _ = _registry()
    a, b, c = _task(), _task(), _task()
    for t in (a, b, c):
        reg.enqueue(t)
    reg.record_start(a.task_id, worker_pid=10)
    reg.record_start(b.task_id, worker_pid=20)
    # c stays QUEUED

    assert [r.task_id for r in reg.running_for_pid(10)] == [a.task_id]
    assert [r.task_id for r in reg.running_for_pid(20)] == [b.task_id]
    assert reg.running_for_pid(30) == []


@pytest.mark.unit
def test_retire_clears_the_running_index() -> None:
    """Stale entries in the running index would fake work that no longer exists."""
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=7)

    reg.retire(task.task_id)

    assert not reg.any_running()
    assert reg.running_for_pid(7) == []


# ---------------------------------------------------------------------------
# expired — the deadline sweep
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_queued_task_has_no_deadline_and_never_expires() -> None:
    """An unclaimed task cannot be judged hung — nothing has started it.

    This is exactly why the deadline sweep alone cannot recover a task lost
    before its heartbeat, and why quiescence detection is a separate mechanism.
    """
    clock = FakeClock()
    reg, _ = _registry(clock)
    reg.enqueue(_task(timeout=1))

    clock.advance(10_000.0)

    assert reg.records()[0].deadline is None
    assert reg.expired() == []


@pytest.mark.unit
def test_expired_fires_after_twice_the_timeout() -> None:
    clock = FakeClock()
    reg, _ = _registry(clock)
    task = _task(timeout=30)
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=5)

    clock.advance(60.0)  # exactly 2x — not yet past
    assert reg.expired() == []

    clock.advance(0.1)
    assert [r.task_id for r in reg.expired()] == [task.task_id]


@pytest.mark.unit
def test_expired_respects_per_task_timeout() -> None:
    """Each task is judged against its own timeout, not a global one."""
    clock = FakeClock()
    reg, _ = _registry(clock)
    quick, slow = _task(timeout=5), _task(timeout=600)
    reg.enqueue(quick)
    reg.enqueue(slow)
    reg.record_start(quick.task_id, worker_pid=1)
    reg.record_start(slow.task_id, worker_pid=2)

    clock.advance(11.0)

    assert [r.task_id for r in reg.expired()] == [quick.task_id]


@pytest.mark.unit
def test_expired_accepts_an_explicit_moment() -> None:
    clock = FakeClock()
    reg, _ = _registry(clock)
    task = _task(timeout=10)
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=1)

    assert reg.expired(now=clock.now + 19.0) == []
    assert len(reg.expired(now=clock.now + 21.0)) == 1


# ---------------------------------------------------------------------------
# redispatch — retry under the same task_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_redispatch_reuses_task_id_and_requeues() -> None:
    """Same id is what lets a late duplicate be recognised and dropped."""
    reg, dispatched = _registry()
    task = _task()
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=3)

    record = reg.redispatch(task.task_id)

    assert record is not None
    assert record.task_id == task.task_id
    assert record.attempt == 1
    assert len(reg) == 1, "re-dispatch must not change the outstanding count"
    assert dispatched == [task, task], "the same Task object goes back on the queue"


@pytest.mark.unit
def test_redispatch_returns_task_to_queued_state() -> None:
    clock = FakeClock()
    reg, _ = _registry(clock)
    task = _task()
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=3)
    clock.advance(7.0)

    record = reg.redispatch(task.task_id)

    assert record is not None
    assert record.started_at is None
    assert record.worker_pid is None
    assert record.enqueued_at == 1007.0, "the age clock restarts for the new attempt"
    assert not reg.any_running()


@pytest.mark.unit
def test_redispatch_unknown_id_returns_none() -> None:
    reg, dispatched = _registry()
    assert reg.redispatch("ghost") is None
    assert dispatched == []


@pytest.mark.unit
def test_redispatch_stops_at_the_retry_budget() -> None:
    """The poison-task guard: a task that kills its worker cannot loop forever."""
    reg, dispatched = _registry(max_retries=3)
    task = _task()
    reg.enqueue(task)

    for expected_attempt in (1, 2, 3):
        record = reg.redispatch(task.task_id)
        assert record is not None
        assert record.attempt == expected_attempt

    assert reg.redispatch(task.task_id) is None
    assert len(dispatched) == 4, "one initial dispatch plus three retries, then no more"


@pytest.mark.unit
def test_budget_exhausted_task_stays_until_retired() -> None:
    """Exhaustion must stay distinguishable from 'never heard of it'.

    redispatch() returning None for both cases is fine only because the record
    is still there to inspect — that is how the caller decides whether to report
    giving up or to ignore an unknown id.
    """
    reg, _ = _registry(max_retries=1)
    task = _task()
    reg.enqueue(task)
    reg.redispatch(task.task_id)

    assert reg.redispatch(task.task_id) is None
    assert len(reg) == 1
    record = reg.get(task.task_id)
    assert record is not None
    assert record.attempt == reg.max_retries

    assert reg.retire(task.task_id) is not None
    assert len(reg) == 0


@pytest.mark.unit
def test_zero_retry_budget_refuses_immediately() -> None:
    reg, dispatched = _registry(max_retries=0)
    task = _task()
    reg.enqueue(task)

    assert reg.redispatch(task.task_id) is None
    assert dispatched == [task]


# ---------------------------------------------------------------------------
# Termination property (defect 2 anti-hang guard)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_registry_drains_within_the_retry_budget() -> None:
    """With no worker ever completing anything, the registry still empties.

    This is the property that makes the coordinator unable to hang: every
    re-dispatch consumes budget, and an exhausted task can be retired.  The old
    crash-orphan sweep had no such bound because it was unreachable at all.
    """
    reg, _ = _registry(max_retries=MAX_RETRIES)
    tasks = [_task() for _ in range(5)]
    for t in tasks:
        reg.enqueue(t)

    sweeps = 0
    while reg and sweeps < 100:
        sweeps += 1
        for record in reg.records():
            if reg.redispatch(record.task_id) is None:
                reg.retire(record.task_id)

    assert len(reg) == 0
    assert sweeps <= MAX_RETRIES + 1, f"took {sweeps} sweeps to drain"


# ---------------------------------------------------------------------------
# TaskRecord
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_record_exposes_task_id_from_its_task() -> None:
    task = _task()
    record = TaskRecord(task=task, enqueued_at=0.0)
    assert record.task_id == task.task_id
    assert not record.is_running
    assert record.deadline is None


@pytest.mark.unit
def test_contains_reflects_membership() -> None:
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)

    assert task.task_id in reg
    reg.retire(task.task_id)
    assert task.task_id not in reg
