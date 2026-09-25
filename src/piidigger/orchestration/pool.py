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

Processes are reached through the ProcessLike protocol and created by an
injected factory, so the pool's bookkeeping is unit-testable with fakes.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
from collections.abc import Callable
from typing import Protocol

from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.worker import worker_loop

# How long to wait for a process to exit after each stop signal.
_STOP_GRACE_SECONDS: float = 2.0


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
        proc = self._active.pop(pid, None)
        if proc is None:
            return None
        self._stop(proc)
        return self._spawn_replacement()

    def reap_dead(self) -> list[tuple[int, int | None]]:
        """Replace every active worker that has died without being asked to.

        Returns one (pid, exitcode) pair per dead worker.  On POSIX a negative
        exit code is the signal that killed the process: -11 is a segfault, the
        signature of a file that crashes a C parser.
        """
        dead = [(pid, proc) for pid, proc in self._active.items() if not proc.is_alive()]
        for pid, _ in dead:
            del self._active[pid]
        for _ in dead:
            self._spawn_replacement()
        return [(pid, proc.exitcode) for pid, proc in dead]

    # -- internals ----------------------------------------------------------

    def _add(self, proc: ProcessLike) -> None:
        if proc.pid is None:
            raise RuntimeError("worker process has no pid; it was not started")
        self._active[proc.pid] = proc

    def _spawn_replacement(self) -> ProcessLike | None:
        """Start one replacement worker, logging rather than raising on failure.

        Starting a process can fail under resource pressure, for example a
        process limit or Windows commit charge.  Letting that escape the
        coordinator loop would abort the whole scan over one lost worker.
        Running one worker short is the better outcome.
        """
        try:
            proc = self._spawn()
            self._add(proc)
        except Exception:
            self._log.exception("could not start a replacement worker; pool now has %d", self.size)
            return None
        return proc

    def _stop(self, proc: ProcessLike) -> None:
        """terminate(), then kill() if that is ignored.  Survivors become stragglers.

        On POSIX, SIGTERM can be ignored and neither signal interrupts
        uninterruptible I/O, so both can fail.  A process still alive after both is
        kept rather than dropped.  Dropping it is how it used to vanish from
        shutdown accounting.
        """
        proc.terminate()
        proc.join(self._grace)
        if proc.is_alive():
            proc.kill()
            proc.join(self._grace)
        if proc.is_alive():
            self._log.warning("worker pid=%s survived terminate() and kill(); keeping it as a straggler", proc.pid)
            self._stragglers.append(proc)


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
