from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from piidigger.models.tasks import TaskProgress

# Least time (seconds) between two "alive" messages.  The deadline is measured
# in tens of seconds, so one a second is plenty, and it keeps a fast prefix skip
# from flooding the result queue.
_ALIVE_INTERVAL: float = 1.0


class ProgressReporter:
    """Sends TaskProgress for one task from inside a worker.

    Event-driven, never clock-driven: a message goes out only when the caller
    reports that something happened — an item started, an item finished, or
    bytes were read.  There is deliberately no timer thread.  A timer would keep
    sending while the worker's main thread was stuck, which would hide exactly
    the hang the coordinator's deadline exists to catch.

    alive() is the only call that is rate-limited, because the archive readers
    call it on every read.  item_started() and item_done() always send, since
    the coordinator relies on each one.  Any message counts as a sign of life,
    so it also restarts the alive() interval.

    Handlers receive the bound methods as plain callables, so archive handlers
    never import multiprocessing or see a queue.
    """

    def __init__(
        self,
        put: Callable[[Any], None],
        task_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        alive_interval: float = _ALIVE_INTERVAL,
    ) -> None:
        self._put = put
        self._task_id = task_id
        self._clock = clock
        self._alive_interval = alive_interval
        self._pid = os.getpid()
        self._last_sent = clock()

    def alive(self) -> None:
        """Report that data is moving.  Sends at most once per alive_interval."""
        if self._clock() - self._last_sent >= self._alive_interval:
            self._send(TaskProgress(task_id=self._task_id, worker_pid=self._pid, event="alive"))

    def item_started(self, item: str) -> None:
        """Report that work on item is starting, so a hang or crash can be pinned on it."""
        self._send(TaskProgress(task_id=self._task_id, worker_pid=self._pid, event="item_started", item=item))

    def item_done(self, item: str, findings: list[dict[str, Any]], counters: dict[str, int]) -> None:
        """Report that item is finished, with its findings and counters."""
        self._send(
            TaskProgress(
                task_id=self._task_id,
                worker_pid=self._pid,
                event="item_done",
                item=item,
                findings=findings,
                counters=counters,
            )
        )

    def _send(self, message: TaskProgress) -> None:
        self._put(message)
        self._last_sent = self._clock()
