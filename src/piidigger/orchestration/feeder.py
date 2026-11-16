"""TaskFeeder — keeps the task queue short and holds the backlog in memory.

An "unbounded" multiprocessing.Queue is capped by SemLock.SEM_VALUE_MAX, which
is 32,767 on macOS.  A directory fan-out can register far more tasks than that.
When the coordinator put them all on the queue, it blocked in put() while the
workers blocked putting results it was not reading: a deadlock.  The feeder puts
only a small window of tasks on the queue and tops it up as each TaskStarted
arrives, so the coordinator never blocks and the queues stay far below the cap.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from piidigger.models.tasks import Task


class TaskFeeder:
    """Puts at most `window` not-yet-started tasks on the task queue at a time.

    TaskRegistry calls submit() as its put callable, so registering a task is
    unchanged.  A task holds a slot from the put until its TaskStarted arrives.

    The backlog is keyed by task_id.  A task re-submitted while still waiting
    (the lost-task sweep re-dispatches outstanding tasks) replaces its entry in
    place instead of joining the backlog a second time.  A task re-submitted
    after it was put frees its old slot first, so a task lost before its
    heartbeat cannot hold a slot forever.
    """

    def __init__(self, put: Callable[[Task], None], window: int) -> None:
        self._put = put
        self._window = max(1, window)
        self._backlog: OrderedDict[str, Task] = OrderedDict()
        self._in_flight: set[str] = set()

    def __len__(self) -> int:
        """Tasks held back, not yet put on the queue."""
        return len(self._backlog)

    @property
    def in_flight(self) -> int:
        """Tasks put on the queue and not yet started."""
        return len(self._in_flight)

    def is_waiting(self, task_id: str) -> bool:
        """True while the task is in the backlog and has never reached the queue."""
        return task_id in self._backlog

    def submit(self, task: Task) -> None:
        self._in_flight.discard(task.task_id)
        self._backlog[task.task_id] = task
        self._pump()

    def started(self, task_id: str) -> None:
        """A worker took the task off the queue, so its slot is free."""
        self._in_flight.discard(task_id)
        self._pump()

    def release(self, task_id: str) -> None:
        """Forget a task the coordinator gave up on before it started."""
        self._in_flight.discard(task_id)
        self._backlog.pop(task_id, None)
        self._pump()

    def _pump(self) -> None:
        while self._backlog and len(self._in_flight) < self._window:
            task_id, task = self._backlog.popitem(last=False)
            self._in_flight.add(task_id)
            self._put(task)
