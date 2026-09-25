"""Unit tests for HealthMonitor — the coordinator's periodic health sweep.

These drive the real TaskRegistry and the real WorkerPool, with fake processes
and a fake clock, so each sweep scenario runs in microseconds.  Before this
refactor the same logic was a closure inside run_coordinator, and the only way
to test it was to copy it into the test, which is how the crash-recovery hang
went unnoticed.
"""

from __future__ import annotations

import logging

import pytest

from piidigger.models.tasks import Task, TaskType
from piidigger.orchestration.coordinator import HealthMonitor, SweepResult
from piidigger.orchestration.pool import WorkerPool
from piidigger.orchestration.registry import MAX_RETRIES, TaskRegistry
from tests._fakes import FakeClock, FakeProcess, Spawner

_LOG = logging.getLogger("tests.health_monitor")


class Harness:
    """A registry, a pool of fake workers, and a monitor, all on one fake clock."""

    def __init__(self, workers: int = 3, max_retries: int = MAX_RETRIES) -> None:
        self.clock = FakeClock()
        self.dispatched: list[Task] = []
        self.registry = TaskRegistry(self.dispatched.append, clock=self.clock, max_retries=max_retries)
        self.spawner = Spawner()
        self.pool = WorkerPool(self.spawner, logger=_LOG, grace=0.0)
        self.pool.start(workers)
        self.monitor = HealthMonitor(self.registry, self.pool, clock=self.clock)

    def worker(self, index: int = 0) -> FakeProcess:
        return self.spawner.spawned[index]

    def run(self, worker: FakeProcess, timeout: int = 30) -> Task:
        """Enqueue a task and mark it RUNNING on the given worker."""
        task = Task(task_type=TaskType.NOOP, timeout_seconds=timeout)
        self.registry.enqueue(task)
        assert worker.pid is not None
        self.registry.record_start(task.task_id, worker.pid)
        return task


# ---------------------------------------------------------------------------
# Nothing to do
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_quiet_tick_reports_nothing() -> None:
    h = Harness()
    h.registry.enqueue(Task(task_type=TaskType.NOOP))

    sweep = h.monitor.tick()

    assert not sweep
    assert len(h.registry) == 1
    assert len(h.spawner.spawned) == 3


@pytest.mark.unit
def test_healthy_running_task_is_left_alone() -> None:
    h = Harness()
    task = h.run(h.worker(0), timeout=30)
    h.clock.advance(59.0)  # inside 2 x timeout

    sweep = h.monitor.tick()

    assert not sweep
    assert task.task_id in h.registry


# ---------------------------------------------------------------------------
# Deadline sweep
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_expired_task_is_abandoned_and_its_worker_replaced() -> None:
    h = Harness()
    hung_worker = h.worker(1)
    task = h.run(hung_worker, timeout=30)
    h.clock.advance(61.0)

    sweep = h.monitor.tick()

    assert [r.task_id for r in sweep.timed_out] == [task.task_id]
    assert len(h.registry) == 0
    assert hung_worker.terminate_calls == 1
    assert hung_worker.pid not in h.pool.pids()
    assert h.pool.size == 3
    assert h.registry.count_abandoned("timed_out") == 1


@pytest.mark.unit
def test_timeouts_are_not_retried() -> None:
    """A task that hung once will most likely hang again, so it is not re-queued."""
    h = Harness()
    task = h.run(h.worker(0), timeout=10)
    h.clock.advance(21.0)

    sweep = h.monitor.tick()

    assert sweep.redispatched == []
    assert h.dispatched == [task], "the task went on the queue once and never again"


@pytest.mark.unit
def test_timed_out_task_can_still_be_reclaimed() -> None:
    """If the hung attempt's result turns up after all, it must not be discarded."""
    h = Harness()
    task = h.run(h.worker(0), timeout=10)
    h.clock.advance(21.0)
    h.monitor.tick()

    assert h.registry.reclaim(task.task_id) is not None


@pytest.mark.unit
def test_hung_worker_that_also_died_is_replaced_once() -> None:
    """The defect 3 sequence: deadline sweep and crash sweep see the same worker.

    The deadline sweep runs first and replaces the worker.  The crash sweep must
    find it already gone, rather than spawning a second replacement.
    """
    h = Harness()
    worker = h.worker(0)
    h.run(worker, timeout=10)
    worker.crash()
    h.clock.advance(21.0)

    sweep = h.monitor.tick()

    assert len(sweep.timed_out) == 1
    assert sweep.crashed == [], "already replaced by the deadline sweep"
    assert len(h.spawner.spawned) == 4, "three initial workers plus exactly one replacement"
    assert h.pool.size == 3


# ---------------------------------------------------------------------------
# Crash sweep
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_on_crashed_worker_is_redispatched_under_the_same_id() -> None:
    h = Harness()
    worker = h.worker(2)
    task = h.run(worker)
    worker.crash(-11)

    sweep = h.monitor.tick()

    assert sweep.crashed == [(worker.pid, -11)]
    assert [r.task_id for r in sweep.redispatched] == [task.task_id]
    record = h.registry.get(task.task_id)
    assert record is not None
    assert record.attempt == 1
    assert not record.is_running
    assert h.dispatched == [task, task]
    assert h.pool.size == 3


@pytest.mark.unit
def test_crash_sweep_ignores_queued_tasks() -> None:
    """A crash says nothing about tasks no worker has claimed yet."""
    h = Harness()
    queued = Task(task_type=TaskType.NOOP)
    h.registry.enqueue(queued)
    h.worker(0).crash()

    sweep = h.monitor.tick()

    assert len(sweep.crashed) == 1
    assert sweep.redispatched == []
    assert h.dispatched == [queued]


@pytest.mark.unit
def test_task_is_abandoned_when_the_retry_budget_is_spent() -> None:
    h = Harness(max_retries=0)
    worker = h.worker(0)
    task = h.run(worker)
    worker.crash()

    sweep = h.monitor.tick()

    assert [r.task_id for r in sweep.abandoned] == [task.task_id]
    assert sweep.redispatched == []
    assert len(h.registry) == 0
    assert h.registry.count_abandoned("crashed") == 1
    assert h.registry.reclaim(task.task_id) is not None


@pytest.mark.unit
def test_late_heartbeat_from_an_already_reaped_worker_is_redispatched() -> None:
    """A heartbeat can arrive after its worker has died and been replaced.

    Tick 1 reaps the dead worker while its task is still QUEUED as far as the
    registry knows.  The heartbeat is read afterwards, marking the task RUNNING
    on a pid that no longer exists.  Tick 2 must treat that as a crash rather
    than wait out the deadline.
    """
    h = Harness()
    doomed = h.worker(0)
    task = Task(task_type=TaskType.NOOP)
    h.registry.enqueue(task)
    doomed.crash()
    first = h.monitor.tick()
    assert [pid for pid, _ in first.crashed] == [doomed.pid]
    assert first.redispatched == []

    assert doomed.pid is not None
    h.registry.record_start(task.task_id, doomed.pid)
    second = h.monitor.tick()

    assert [r.task_id for r in second.redispatched] == [task.task_id]


@pytest.mark.unit
def test_poison_task_is_abandoned_within_the_retry_budget() -> None:
    """A task that kills every worker it lands on must not loop forever.

    Each attempt: a live worker picks the task up, then dies.  The monitor must
    give up after MAX_RETRIES re-dispatches, which is what bounds the scan.
    """
    h = Harness()
    task = Task(task_type=TaskType.NOOP)
    h.registry.enqueue(task)

    ticks = 0
    while task.task_id in h.registry and ticks < 50:
        worker_pid = next(iter(h.pool.pids()))
        h.registry.record_start(task.task_id, worker_pid)
        next(p for p in h.spawner.spawned if p.pid == worker_pid).crash()
        sweep = h.monitor.tick()
        ticks += 1

    assert task.task_id not in h.registry
    assert ticks == MAX_RETRIES + 1
    assert [r.task_id for r in sweep.abandoned] == [task.task_id]
    assert h.pool.size == 3, "every dead worker was replaced along the way"


# ---------------------------------------------------------------------------
# SweepResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_sweep_result_is_falsy() -> None:
    assert not SweepResult(now=0.0)
    assert SweepResult(now=0.0, crashed=[(1, -11)])
