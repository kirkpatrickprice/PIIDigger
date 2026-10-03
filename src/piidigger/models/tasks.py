from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict, Field

from piidigger.models.base import PiiDiggerModel


class TaskType(StrEnum):
    ENUM_DIR = "enum_dir"
    SCAN_FILE = "scan_file"
    NOOP = "noop"  # kept for integration tests; pass {"delay_seconds": N} in payload to simulate slow tasks
    ENUM_ARCHIVE_MEMBERS = "enum_archive_members"
    SCAN_ARCHIVE_MEMBERS = "scan_archive_members"


class Task(PiiDiggerModel):
    """One unit of dispatched work.

    items is the task's work list when it covers several things — for a
    SCAN_ARCHIVE_MEMBERS batch, the member paths still to scan, in archive
    order.  The registry shrinks it as TaskProgress reports each item done, so a
    re-queued copy starts where the last attempt stopped.  The registry never
    reads the payload; items is the only part of a task it changes.  Empty for
    task types that do one thing.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    task_type: TaskType
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    items: tuple[str, ...] = ()


class TaskResult(PiiDiggerModel):
    task_id: str
    task_type: TaskType
    status: Literal["ok", "timeout", "error"]
    new_tasks: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None
    duration_seconds: float = Field(default=0.0, ge=0.0)
    worker_pid: int | None = None


type ProgressEvent = Literal["item_started", "item_done", "alive"]


class TaskProgress(PiiDiggerModel):
    """Placed on result_queue while a long task runs, between TaskStarted and TaskResult.

    * item_started: the worker is about to work on `item`.  If the worker then
      hangs or dies, the coordinator knows which item to blame.
    * item_done: `item` is finished.  Carries that item's findings and
      counters, so they reach the output even if a later item hangs.
    * alive: the worker is reading data but has not finished an item.  Sent at
      most once a second, and only when bytes actually move — never from a
      timer — so a stuck worker goes quiet and its deadline still fires.

    Every event from the worker that holds the task pushes its deadline back.

    A Pydantic model rather than a dataclass, because findings come from file
    content.  TaskStarted, which carries only values we generate, stays a
    dataclass.
    """

    task_id: str
    worker_pid: int
    event: ProgressEvent
    item: str | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True)
class TaskStarted:
    """Heartbeat placed on result_queue when a worker dequeues a task.

    Not a TaskResult — it does not change the coordinator's pending count.
    The coordinator uses it to record dispatch time for deadline monitoring.
    """

    task_id: str
    worker_pid: int


@dataclass(frozen=True)
class WorkerReady:
    """Placed on result_queue once by each worker, when it has started up.

    Not a TaskResult and not a heartbeat: it changes no task's state.  It lets
    the coordinator tell a worker that is still starting from one that is
    waiting for work.  Both are silent otherwise, and the lost-task sweep needs
    to tell them apart.
    """

    worker_pid: int


@dataclass(frozen=True)
class ShutdownSentinel:
    """Placed on task_queue once per worker to signal graceful exit.

    Identified by isinstance() in the worker loop because pickle/unpickle
    across the spawn boundary creates a new instance (identity checks fail).
    Broadcast N times by broadcast_shutdown() — one per worker process.
    """


SHUTDOWN: ShutdownSentinel = ShutdownSentinel()
