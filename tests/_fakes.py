"""Test doubles shared by the registry, pool, and health-monitor unit tests.

None of these start a process or sleep, which is what lets the orchestration
bookkeeping be tested in milliseconds.
"""

from __future__ import annotations

import itertools


class FakeClock:
    """Monotonic clock under test control, so deadlines need no real waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


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

    def exit_cleanly(self) -> None:
        """Exit with status 0, as a worker does on receiving a shutdown sentinel."""
        self._die(0)

    def _die(self, exitcode: int) -> None:
        if self._alive:
            self._alive = False
            self.exitcode = exitcode


class Spawner:
    """Process factory that records everything it starts and can be told to fail."""

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
