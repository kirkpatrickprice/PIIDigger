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
from tests._fakes import FakeClock


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
# running — the crash sweep's view of work in progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_running_lists_only_started_tasks() -> None:
    reg, _ = _registry()
    a, b, c = _task(), _task(), _task()
    for t in (a, b, c):
        reg.enqueue(t)
    reg.record_start(a.task_id, worker_pid=10)
    reg.record_start(b.task_id, worker_pid=20)
    # c stays QUEUED

    assert {r.task_id: r.worker_pid for r in reg.running()} == {a.task_id: 10, b.task_id: 20}


@pytest.mark.unit
def test_retire_clears_the_running_index() -> None:
    """Stale entries in the running index would fake work that no longer exists."""
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=7)

    reg.retire(task.task_id)

    assert not reg.any_running()
    assert reg.running() == []


# ---------------------------------------------------------------------------
# abandon / reclaim — keeping a late result for a task we gave up on
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_abandon_retires_the_task() -> None:
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)
    reg.record_start(task.task_id, worker_pid=3)

    record = reg.abandon(task.task_id, reason="timed_out")

    assert record is not None
    assert len(reg) == 0
    assert not reg.any_running()


@pytest.mark.unit
def test_reclaim_returns_an_abandoned_record_once() -> None:
    """A late result for an abandoned task is accepted exactly once."""
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)
    reg.abandon(task.task_id, reason="crashed")

    first = reg.reclaim(task.task_id)
    assert first is not None
    assert first.task_id == task.task_id
    assert reg.reclaim(task.task_id) is None
    assert len(reg) == 0, "reclaiming must not put the task back in the registry"


@pytest.mark.unit
def test_reclaim_refuses_a_task_that_was_merely_retired() -> None:
    """A normally retired task's second result is a duplicate, not a lost report."""
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)
    reg.retire(task.task_id)

    assert reg.reclaim(task.task_id) is None


@pytest.mark.unit
def test_abandon_unknown_id_returns_none() -> None:
    reg, _ = _registry()
    assert reg.abandon("ghost", reason="timed_out") is None
    assert reg.reclaim("ghost") is None


@pytest.mark.unit
def test_count_abandoned_separates_reasons() -> None:
    reg, _ = _registry()
    hung, poison_a, poison_b = _task(), _task(), _task()
    for t in (hung, poison_a, poison_b):
        reg.enqueue(t)
    reg.abandon(hung.task_id, reason="timed_out")
    reg.abandon(poison_a.task_id, reason="crashed")
    reg.abandon(poison_b.task_id, reason="crashed")

    assert reg.count_abandoned("timed_out") == 1
    assert reg.count_abandoned("crashed") == 2


@pytest.mark.unit
def test_reclaimed_task_no_longer_counts_as_abandoned() -> None:
    """A late result means the work did complete, so it must leave the tally."""
    reg, _ = _registry()
    task = _task()
    reg.enqueue(task)
    reg.abandon(task.task_id, reason="timed_out")

    reg.reclaim(task.task_id)

    assert reg.count_abandoned("timed_out") == 0


# ---------------------------------------------------------------------------
# expired — the deadline sweep
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_queued_task_has_no_deadline_and_never_expires() -> None:
    """An unclaimed task cannot be judged hung — nothing has started it.

    This is exactly why the deadline sweep alone cannot recover a task lost
    before its heartbeat, and why the lost-task sweep is a separate mechanism.
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


# ---------------------------------------------------------------------------
# Batches — tasks with items, and TaskProgress
# ---------------------------------------------------------------------------

_PID = 4242


def _batch(*items: str, timeout: int = 30) -> Task:
    return Task(task_type=TaskType.SCAN_ARCHIVE_MEMBERS, timeout_seconds=timeout, items=items)


def _running_batch(*items: str) -> tuple[TaskRegistry, list[Task], FakeClock, Task]:
    clock = FakeClock()
    reg, dispatched = _registry(clock)
    task = _batch(*items)
    reg.enqueue(task)
    reg.record_start(task.task_id, _PID)
    return reg, dispatched, clock, task


@pytest.mark.unit
def test_progress_from_the_holder_pushes_the_deadline_back() -> None:
    reg, _, clock, task = _running_batch("a", "b")
    clock.advance(50.0)
    reg.record_progress(task.task_id, _PID, "alive", None)
    clock.advance(50.0)  # 100 s since start, 50 s since progress

    assert reg.expired() == []
    clock.advance(11.0)  # 61 s of silence
    assert [r.task_id for r in reg.expired()] == [task.task_id]


@pytest.mark.unit
def test_progress_from_another_pid_does_not_extend_the_deadline() -> None:
    """A superseded copy of a task must not keep the current attempt alive."""
    reg, _, clock, task = _running_batch("a")
    clock.advance(50.0)
    reg.record_progress(task.task_id, _PID + 1, "alive", None)
    clock.advance(11.0)

    assert [r.task_id for r in reg.expired()] == [task.task_id]


@pytest.mark.unit
def test_item_done_is_accepted_once() -> None:
    reg, _, _, task = _running_batch("a", "b")

    assert reg.record_progress(task.task_id, _PID, "item_done", "a") is True
    assert reg.record_progress(task.task_id, _PID, "item_done", "a") is False, "a duplicate must be dropped"
    record = reg.get(task.task_id)
    assert record is not None
    assert list(record.remaining) == ["b"]


@pytest.mark.unit
def test_item_started_marks_the_current_item_only_for_the_holder() -> None:
    reg, _, _, task = _running_batch("a", "b")
    record = reg.get(task.task_id)
    assert record is not None

    reg.record_progress(task.task_id, _PID + 1, "item_started", "a")
    assert record.current_item is None
    reg.record_progress(task.task_id, _PID, "item_started", "a")
    assert record.current_item == "a"
    reg.record_progress(task.task_id, _PID, "item_done", "a")
    assert record.current_item is None


@pytest.mark.unit
def test_requeue_remaining_drops_the_culprit_and_requeues_the_rest() -> None:
    reg, dispatched, _, task = _running_batch("a", "b", "c", "d")
    reg.record_progress(task.task_id, _PID, "item_done", "a")
    reg.record_progress(task.task_id, _PID, "item_started", "b")

    culprit = reg.requeue_remaining(task.task_id, reason="timed_out")

    assert culprit == "b"
    requeued = dispatched[-1]
    assert requeued.task_id == task.task_id
    assert requeued.items == ("c", "d"), "the requeued batch starts after the culprit"
    record = reg.get(task.task_id)
    assert record is not None
    assert record.attempt == 0, "dropping a culprit uses no retry budget"
    assert not record.is_running
    assert reg.count_abandoned("timed_out") == 1
    assert reg.count_unfinished() == 2


@pytest.mark.unit
def test_requeue_remaining_without_an_item_in_progress_does_nothing() -> None:
    reg, dispatched, _, task = _running_batch("a", "b")

    assert reg.requeue_remaining(task.task_id, reason="crashed") is None
    assert dispatched == [task]


@pytest.mark.unit
def test_requeue_remaining_on_the_last_item_abandons_the_task() -> None:
    reg, dispatched, _, task = _running_batch("a")
    reg.record_progress(task.task_id, _PID, "item_started", "a")

    assert reg.requeue_remaining(task.task_id, reason="crashed") == "a"
    assert len(reg) == 0
    assert dispatched == [task], "nothing left to requeue"
    assert reg.count_abandoned("crashed") == 1


@pytest.mark.unit
def test_late_item_done_for_a_skipped_item_is_accepted_and_uncounted() -> None:
    """The item_done was already on the queue when the sweep blamed the item."""
    reg, _, _, task = _running_batch("a", "b")
    reg.record_progress(task.task_id, _PID, "item_started", "a")
    reg.requeue_remaining(task.task_id, reason="timed_out")

    assert reg.record_progress(task.task_id, _PID, "item_done", "a") is True
    assert reg.record_progress(task.task_id, _PID, "item_done", "a") is False
    assert reg.count_abandoned("timed_out") == 0


@pytest.mark.unit
def test_late_item_done_for_an_abandoned_batch_is_accepted_once() -> None:
    reg, _, _, task = _running_batch("a", "b")
    reg.abandon(task.task_id, reason="timed_out")
    assert reg.count_abandoned("timed_out") == 2, "an abandoned batch counts its unfinished members"

    assert reg.record_progress(task.task_id, _PID, "item_done", "a") is True
    assert reg.record_progress(task.task_id, _PID, "item_done", "a") is False
    assert reg.count_abandoned("timed_out") == 1


@pytest.mark.unit
def test_redispatch_trims_a_batch_to_what_remains() -> None:
    reg, dispatched, _, task = _running_batch("a", "b", "c")
    reg.record_progress(task.task_id, _PID, "item_done", "a")

    reg.redispatch(task.task_id)

    assert dispatched[-1].items == ("b", "c")
    record = reg.get(task.task_id)
    assert record is not None
    assert record.attempt == 1


@pytest.mark.unit
def test_progress_for_an_unknown_task_is_rejected() -> None:
    reg, _ = _registry()
    assert reg.record_progress("nope", _PID, "item_done", "a") is False
    assert reg.record_progress("nope", _PID, "alive", None) is False


@pytest.mark.unit
def test_requeue_remaining_after_an_error_result_keeps_no_skip_record() -> None:
    """The worker reported the failure itself; the caller counts it, not the registry."""
    reg, dispatched, _, task = _running_batch("a", "b")
    reg.record_progress(task.task_id, _PID, "item_started", "a")

    assert reg.requeue_remaining(task.task_id, reason=None) == "a"
    assert dispatched[-1].items == ("b",)
    assert reg.count_abandoned("timed_out") == reg.count_abandoned("crashed") == 0


@pytest.mark.unit
def test_requeue_remaining_after_an_error_on_the_last_item_retires_the_task() -> None:
    reg, dispatched, _, task = _running_batch("a")
    reg.record_progress(task.task_id, _PID, "item_started", "a")

    assert reg.requeue_remaining(task.task_id, reason=None) == "a"
    assert len(reg) == 0
    assert dispatched == [task]
    assert reg.count_abandoned("crashed") == 0
