"""Unit tests for WorkerPool — spawning, replacing, and stopping workers.

Every test uses fake processes, so none of them starts a real worker.  The
pool's job is bookkeeping, and bookkeeping is where the double-replacement and
silently-dropped-worker defects lived.
"""

from __future__ import annotations

import itertools
import logging

import pytest

from piidigger.orchestration import pool as pool_module
from piidigger.orchestration.pool import WorkerPool, spawn_worker

_LOG = logging.getLogger("tests.worker_pool")


class FakeProcess:
    """A process stand-in whose response to stop signals is configurable."""

    _pids = itertools.count(1000)

    def __init__(self) -> None:
        self.pid: int | None = next(FakeProcess._pids)
        self.exitcode: int | None = None
        self.obeys_terminate = True
        self.obeys_kill = True
        self.terminate_calls = 0
        self.kill_calls = 0
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.obeys_terminate:
            self._die(-15)

    def kill(self) -> None:
        self.kill_calls += 1
        if self.obeys_kill:
            self._die(-9)

    def join(self, timeout: float | None = None) -> None:
        pass

    def crash(self, exitcode: int = -11) -> None:
        """Die unprompted, as a worker does when a C extension segfaults."""
        self._die(exitcode)

    def _die(self, exitcode: int) -> None:
        if self._alive:
            self._alive = False
            self.exitcode = exitcode


class Spawner:
    """Factory that records every process it starts and can be told to fail."""

    def __init__(self) -> None:
        self.spawned: list[FakeProcess] = []
        self.failures_remaining = 0

    def __call__(self) -> FakeProcess:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise OSError("resource temporarily unavailable")
        proc = FakeProcess()
        self.spawned.append(proc)
        return proc


def _pool(n: int = 3) -> tuple[WorkerPool, Spawner]:
    spawner = Spawner()
    pool = WorkerPool(spawner, logger=_LOG, grace=0.0)
    pool.start(n)
    return pool, spawner


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_start_spawns_requested_workers() -> None:
    pool, spawner = _pool(4)

    assert pool.size == 4
    assert len(spawner.spawned) == 4
    assert pool.pids() == {p.pid for p in spawner.spawned}


@pytest.mark.unit
def test_start_propagates_spawn_failure() -> None:
    """A pool that cannot start at all should fail the scan loudly."""
    spawner = Spawner()
    spawner.failures_remaining = 1
    pool = WorkerPool(spawner, logger=_LOG, grace=0.0)

    with pytest.raises(OSError, match="resource temporarily unavailable"):
        pool.start(2)


# ---------------------------------------------------------------------------
# replace — deliberate stop plus one replacement
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_replace_stops_old_worker_and_starts_one() -> None:
    pool, spawner = _pool(3)
    victim = spawner.spawned[0]
    assert victim.pid is not None

    replacement = pool.replace(victim.pid)

    assert replacement is not None
    assert victim.terminate_calls == 1
    assert not victim.is_alive()
    assert victim.pid not in pool.pids()
    assert replacement.pid in pool.pids()
    assert pool.size == 3
    assert len(spawner.spawned) == 4


@pytest.mark.unit
def test_replace_unknown_pid_starts_nothing() -> None:
    """The defect 3 guard: an already-replaced pid must not spawn a second worker."""
    pool, spawner = _pool(2)

    assert pool.replace(999_999) is None
    assert len(spawner.spawned) == 2
    assert pool.size == 2


@pytest.mark.unit
def test_replace_same_pid_twice_replaces_once() -> None:
    pool, spawner = _pool(2)
    pid = spawner.spawned[0].pid
    assert pid is not None

    assert pool.replace(pid) is not None
    assert pool.replace(pid) is None
    assert len(spawner.spawned) == 3, "two initial workers plus exactly one replacement"
    assert pool.size == 2


@pytest.mark.unit
def test_timeout_then_crash_sweep_on_same_dead_worker_replaces_once() -> None:
    """The exact defect 3 sequence, in the order the health sweep runs.

    A worker dies while RUNNING a task.  The deadline sweep runs first and
    replaces it for the timed-out task; the crash sweep then runs and must not
    replace the same worker again.  The old coordinator did exactly that, growing
    the pool by one per occurrence.
    """
    pool, spawner = _pool(3)
    worker = spawner.spawned[1]
    assert worker.pid is not None
    worker.crash()

    assert pool.replace(worker.pid) is not None  # deadline sweep
    assert pool.reap_dead() == []  # crash sweep finds nothing left to do

    assert pool.size == 3
    assert len(spawner.spawned) == 4


@pytest.mark.unit
def test_crash_sweep_then_replace_on_same_worker_replaces_once() -> None:
    """The reverse order is guarded too."""
    pool, spawner = _pool(3)
    worker = spawner.spawned[0]
    assert worker.pid is not None
    worker.crash()

    assert [pid for pid, _ in pool.reap_dead()] == [worker.pid]
    assert pool.replace(worker.pid) is None

    assert pool.size == 3
    assert len(spawner.spawned) == 4


@pytest.mark.unit
def test_pool_size_holds_across_many_replacements() -> None:
    pool, _ = _pool(4)
    for _ in range(50):
        pool.replace(next(iter(pool.pids())))
    assert pool.size == 4


# ---------------------------------------------------------------------------
# reap_dead — unexpected deaths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reap_dead_replaces_crashed_workers_and_reports_exit_codes() -> None:
    pool, spawner = _pool(4)
    segfaulted, exited = spawner.spawned[0], spawner.spawned[2]
    segfaulted.crash(-11)
    exited.crash(1)

    reaped = dict(pool.reap_dead())

    assert reaped == {segfaulted.pid: -11, exited.pid: 1}
    assert pool.size == 4
    assert segfaulted.pid not in pool.pids()
    assert exited.pid not in pool.pids()


@pytest.mark.unit
def test_reap_dead_with_no_deaths_does_nothing() -> None:
    pool, spawner = _pool(3)

    assert pool.reap_dead() == []
    assert len(spawner.spawned) == 3


@pytest.mark.unit
def test_reap_dead_does_not_signal_the_dead() -> None:
    """A worker that died on its own is replaced, not sent stop signals."""
    pool, spawner = _pool(2)
    worker = spawner.spawned[0]
    worker.crash()

    pool.reap_dead()

    assert worker.terminate_calls == 0
    assert worker.kill_calls == 0


# ---------------------------------------------------------------------------
# Stop escalation and stragglers (defect 7)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_replace_escalates_to_kill_when_terminate_is_ignored() -> None:
    pool, spawner = _pool(2)
    stubborn = spawner.spawned[0]
    stubborn.obeys_terminate = False
    assert stubborn.pid is not None

    pool.replace(stubborn.pid)

    assert stubborn.terminate_calls == 1
    assert stubborn.kill_calls == 1
    assert not stubborn.is_alive()
    assert pool.stragglers == []


@pytest.mark.unit
def test_kill_is_not_sent_when_terminate_works() -> None:
    pool, spawner = _pool(1)
    worker = spawner.spawned[0]
    assert worker.pid is not None

    pool.replace(worker.pid)

    assert worker.kill_calls == 0


@pytest.mark.unit
def test_worker_surviving_kill_is_kept_as_a_straggler() -> None:
    """The defect 7 guard: a survivor must stay visible to teardown.

    The old coordinator removed a terminated worker from its list without
    checking that it had actually exited.  A survivor then received no shutdown
    sentinel and, being non-daemonic, blocked interpreter exit.
    """
    pool, spawner = _pool(3)
    unkillable = spawner.spawned[1]
    unkillable.obeys_terminate = False
    unkillable.obeys_kill = False
    assert unkillable.pid is not None

    replacement = pool.replace(unkillable.pid)

    assert replacement is not None, "the pool still gets its replacement"
    assert pool.size == 3
    assert unkillable.pid not in pool.pids(), "a straggler is not an active worker"
    assert pool.stragglers == [unkillable]
    assert unkillable in pool.processes, "teardown must still see it"
    assert len(pool.processes) == 4


@pytest.mark.unit
def test_straggler_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    pool, spawner = _pool(1)
    unkillable = spawner.spawned[0]
    unkillable.obeys_terminate = False
    unkillable.obeys_kill = False
    assert unkillable.pid is not None

    with caplog.at_level(logging.WARNING, logger=_LOG.name):
        pool.replace(unkillable.pid)

    assert "straggler" in caplog.text
    assert str(unkillable.pid) in caplog.text


# ---------------------------------------------------------------------------
# Replacement spawn failures
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_replacement_spawn_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """One failed replacement must not abort the scan; the pool runs one short."""
    pool, spawner = _pool(3)
    victim = spawner.spawned[0]
    assert victim.pid is not None
    spawner.failures_remaining = 1

    with caplog.at_level(logging.ERROR, logger=_LOG.name):
        result = pool.replace(victim.pid)

    assert result is None
    assert pool.size == 2
    assert "could not start a replacement worker" in caplog.text


@pytest.mark.unit
def test_reap_dead_survives_spawn_failure() -> None:
    pool, spawner = _pool(3)
    spawner.spawned[0].crash()
    spawner.spawned[1].crash()
    spawner.failures_remaining = 1

    reaped = pool.reap_dead()

    assert len(reaped) == 2
    assert pool.size == 2, "one replacement failed, one succeeded"


@pytest.mark.unit
def test_pool_recovers_after_a_failed_replacement() -> None:
    pool, spawner = _pool(2)
    first = spawner.spawned[0]
    assert first.pid is not None
    spawner.failures_remaining = 1
    pool.replace(first.pid)
    assert pool.size == 1

    second = next(iter(pool.pids()))
    assert pool.replace(second) is not None
    assert pool.size == 1, "a later replacement works; the earlier loss is not made up"


# ---------------------------------------------------------------------------
# spawn_worker — the single process-creation site
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spawn_worker_starts_a_daemonic_worker_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workers are daemonic so a survivor cannot block interpreter exit."""
    created: list[RecordingProcess] = []

    class RecordingProcess:
        def __init__(self, *, target: object, args: tuple[object, ...], daemon: bool) -> None:
            self.target = target
            self.args = args
            self.daemon = daemon
            self.started = False
            created.append(self)

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(pool_module.mp, "Process", RecordingProcess)
    ctx = object()

    proc = spawn_worker(ctx)  # type: ignore[arg-type]

    assert created == [proc]
    assert proc.daemon is True
    assert proc.target is pool_module.worker_loop
    assert proc.args == (ctx,)
    assert proc.started
