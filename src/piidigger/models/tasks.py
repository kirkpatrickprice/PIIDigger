from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskType(StrEnum):
    ENUM_DIR = "enum_dir"
    SCAN_FILE = "scan_file"
    NOOP = "noop"
    # Archive types arrive with ZIP (Phase 5) — no orchestration change required:
    # ENUM_ARCHIVE_MEMBERS = "enum_archive_members"
    # SCAN_ARCHIVE_MEMBER = "scan_archive_member"


class Task(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    task_type: TaskType
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=600)


class TaskResult(BaseModel):
    task_id: str
    task_type: TaskType
    status: Literal["ok", "timeout", "error"]
    new_tasks: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None
    duration_seconds: float = Field(default=0.0, ge=0.0)
    worker_pid: int | None = None


@dataclass(frozen=True)
class TaskStarted:
    """Heartbeat placed on result_queue when a worker dequeues a task.

    Not a TaskResult — it does not change the coordinator's pending count.
    The coordinator uses it to record dispatch time for deadline monitoring.
    """

    task_id: str
    worker_pid: int


@dataclass(frozen=True)
class ShutdownSentinel:
    """Placed on task_queue once per worker to signal graceful exit.

    Identified by isinstance() in the worker loop because pickle/unpickle
    across the spawn boundary creates a new instance (identity checks fail).
    Broadcast N times by broadcast_shutdown() — one per worker process.
    """


SHUTDOWN: ShutdownSentinel = ShutdownSentinel()
