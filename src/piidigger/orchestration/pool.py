"""Ownership of the worker processes: starting, replacing, and stopping them.

WorkerPool is the one place worker processes are started or torn down during a
run.  Centralising that turns two guarantees into structure rather than
something every call site has to remember:

* A worker is replaced at most once.  Its pid leaves the pool *before* a
  replacement is started, so a second path that later notices the same death —
  the crash sweep running after the deadline sweep, say — finds nothing to
  replace.
* A worker is never silently forgotten.  Stopping escalates from terminate() to
  kill(), and a process that survives both is kept as a straggler rather than
  dropped, so teardown can still see it.
* Replacement stops when workers cannot start.  A worker that dies before
  checking in never came up, and replacing it just repeats the failure every
  sweep, forever.

The pool also records which workers have checked in, meaning sent WorkerReady.
That lives here, next to the process it describes, so a new worker always
starts unchecked, even if the OS hands it a pid a dead worker once had.

Processes are reached through the ProcessLike protocol and created by an
injected factory, so the pool's bookkeeping is unit-testable with fakes.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import time
from collections.abc import Callable
from typing import Protocol

from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.worker import worker_loop

# How long to wait for a process to exit after each stop signal.
_STOP_GRACE_SECONDS: float = 2.0

# Consecutive workers that may die before checking in before the pool stops
# replacing them.  A worker that never checks in did not come up at all — a
# broken install, a quarantined DLL, an import error.
_MAX_STARTUP_FAILURES: int = 3


class ProcessLike(Protocol):
    """The subset of multiprocessing.Process that the pool relies on."""

    @property
    def pid(self) -> int | None: ...

    @property
    def exitcode(self) -> int | None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


class WorkerPool:
    """The live worker processes, plus any that refused to die.

    size counts active workers only.  processes adds the stragglers, and it is
    what teardown should join and signal: a straggler may yet wake up and read
    from the task queue.
    """

    def __init__(
        self,
        spawn: Callable[[], ProcessLike],
        *,
        logger: logging.Logger,
        grace: float = _STOP_GRACE_SECONDS,
    ) -> None:
        """Build an empty pool; call start() to populate it.

        Args:
            spawn: Starts one worker and returns it.  spawn_worker bound to a
                context in production; a fake factory in tests.
            logger: Required rather than defaulted.  In the coordinator process a
                bare module logger has no handler, so Python's last-resort
                handler would print to stderr and corrupt the rich.Live display.
            grace: Seconds to wait for exit after terminate(), and again after kill().
        """
        self._spawn = spawn
        self._log = logger
        self._grace = grace
        self._active: dict[int, ProcessLike] = {}
        self._stragglers: list[ProcessLike] = []
        self._not_checked_in: set[int] = set()  # active workers yet to send WorkerReady
        self._startup_failures = 0  # consecutive deaths of workers that never checked in
        self._replacing = True

    # -- inspection ---------------------------------------------------------

    @property
    def size(self) -> int:
        """Active workers.  Stragglers are not counted."""
        return len(self._active)

    def pids(self) -> set[int]:
        """Pids of the active workers."""
        return set(self._active)

    @property
    def processes(self) -> list[ProcessLike]:
        """Every process the pool is responsible for, stragglers included."""
        return [*self._active.values(), *self._stragglers]

    @property
    def stragglers(self) -> list[ProcessLike]:
        """Processes that survived both terminate() and kill()."""
        return list(self._stragglers)

    @property
    def replacing(self) -> bool:
        """False once too many workers in a row died before checking in."""
        return self._replacing

    def all_checked_in(self) -> bool:
        """True when every active worker has checked in.

        Vacuously true with no active workers, which is correct for the
        lost-task sweep: with no workers, nothing can be holding a task.
        """
        return not self._not_checked_in

    def check_in(self, pid: int) -> None:
        """Record that a worker is up.  Ignored for a pid not in the pool.

        Called for WorkerReady, and also for TaskStarted: a worker that has
        started a task has certainly come up.
        """
        if pid in self._not_checked_in:
            self._not_checked_in.discard(pid)
            self._startup_failures = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self, n: int) -> None:
        """Start the initial n workers.

        A failure here propagates.  A scan that cannot start its pool should fail
        loudly rather than run short-handed from the first second.
        """
        for _ in range(n):
            self._add(self._spawn())

    def replace(self, pid: int) -> ProcessLike | None:
        """Stop the worker with this pid and start one in its place.

        Returns the replacement, or None when nothing was started.  That is
        either because the pid is no longer in the pool — it was already
        replaced, which is the double-replacement guard — or because starting
        the replacement failed.  Safe to call on a worker that has already died.
        """
        proc = self._active.get(pid)
        if proc is None:
            return None
        self._forget(pid)
        self._stop([proc])
        return self._spawn_replacement()

    def reap_dead(self) -> list[tuple[int, int | None]]:
        """Replace every active worker that has died without being asked to.

        Returns one (pid, exitcode) pair per dead worker.  On POSIX a negative
        exit code is the signal that killed the process: -11 is a segfault, the
        signature of a file that crashes a C parser.
        """
        dead = [(pid, proc) for pid, proc in self._active.items() if not proc.is_alive()]
        # Count startup failures for the whole batch before replacing any of it,
        # so the breaker applies to this batch rather than the next one.
        for pid, _ in dead:
            if self._forget(pid):
                self._note_startup_failure()
        for _ in dead:
            self._spawn_replacement()
        return [(pid, proc.exitcode) for pid, proc in dead]

    # -- teardown -----------------------------------------------------------

    def terminate_all(self) -> None:
        """Send terminate() to every live process without waiting.

        For the interrupt path, where the goal is to stop work immediately and
        join() follows with a short budget.
        """
        for proc in self.processes:
            if proc.is_alive():
                proc.terminate()

    def join(self, timeout: float) -> None:
        """Wait for every process to exit, then stop whatever is still running.

        timeout is one budget shared by all processes, not a per-process limit,
        so N workers do not multiply the wait.  Active workers still alive when
        it runs out go through the same terminate()/kill() escalation as
        replace(), and any that survive become stragglers.  Existing stragglers
        have already survived both signals, so they are waited on but not
        signalled again.
        """
        self._wait(self.processes, timeout)
        stuck = [(pid, proc) for pid, proc in self._active.items() if proc.is_alive()]
        for pid, _ in stuck:
            self._log.warning("worker pid=%d did not exit within %.1fs; stopping it", pid, timeout)
            self._forget(pid)
        self._stop([proc for _, proc in stuck])

    # -- internals ----------------------------------------------------------

    def _add(self, proc: ProcessLike) -> None:
        if proc.pid is None:
            raise RuntimeError("worker process has no pid; it was not started")
        self._active[proc.pid] = proc
        self._not_checked_in.add(proc.pid)

    def _forget(self, pid: int) -> bool:
        """Drop a worker from the active set.  True if it never checked in."""
        del self._active[pid]
        never_checked_in = pid in self._not_checked_in
        self._not_checked_in.discard(pid)
        return never_checked_in

    def _note_startup_failure(self) -> None:
        self._startup_failures += 1
        if self._replacing and self._startup_failures >= _MAX_STARTUP_FAILURES:
            self._replacing = False
            self._log.error(
                "%d workers in a row died before checking in; no longer replacing workers",
                self._startup_failures,
            )

    def _spawn_replacement(self) -> ProcessLike | None:
        """Start one replacement worker, logging rather than raising on failure.

        Starting a process can fail under resource pressure, for example a
        process limit or Windows commit charge.  Letting that escape the
        coordinator loop would abort the whole scan over one lost worker.
        Running one worker short is the better outcome.

        Returns None without trying once replacement has been switched off.
        """
        if not self._replacing:
            return None
        try:
            proc = self._spawn()
            self._add(proc)
        except Exception:
            self._log.exception("could not start a replacement worker; pool now has %d", self.size)
            return None
        return proc

    def _stop(self, procs: list[ProcessLike]) -> None:
        """terminate(), then kill() whatever ignores it.  Survivors become stragglers.

        The whole batch is signalled before any waiting, so N stuck workers cost
        two grace periods rather than 2N.  On POSIX, SIGTERM can be ignored and
        neither signal interrupts uninterruptible I/O, so both can fail.  A
        process still alive after both is kept rather than dropped.  Dropping it
        is how a worker used to vanish from shutdown accounting.
        """
        for proc in procs:
            proc.terminate()
        self._wait(procs, self._grace)
        stubborn = [proc for proc in procs if proc.is_alive()]
        for proc in stubborn:
            proc.kill()
        self._wait(stubborn, self._grace)
        for proc in stubborn:
            if proc.is_alive():
                self._log.warning("worker pid=%s survived terminate() and kill(); keeping it as a straggler", proc.pid)
                self._stragglers.append(proc)

    @staticmethod
    def _wait(procs: list[ProcessLike], timeout: float) -> None:
        """Join each process, with one shared deadline for the whole list."""
        deadline = time.monotonic() + timeout
        for proc in procs:
            proc.join(max(0.0, deadline - time.monotonic()))


def spawn_worker(ctx: WorkerContext) -> mp.Process:
    """Start one worker process.  The only place a worker process is created.

    daemon=True is a backstop, not a shutdown mechanism.  Python joins a
    non-daemonic child at interpreter exit.  So a worker that survives every stop
    signal would hang piidigger after the scan has reported success.  One way to
    get such a worker is blocking in uninterruptible I/O on a hung network mount.
    Python terminates a daemonic child at exit instead.  Daemonic processes may
    not start children of their own, and workers never do.
    """
    proc = mp.Process(target=worker_loop, args=(ctx,), daemon=True)
    proc.start()
    return proc
