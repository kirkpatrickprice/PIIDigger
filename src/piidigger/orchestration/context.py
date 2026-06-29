from __future__ import annotations

import multiprocessing as mp
import multiprocessing.synchronize
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from piidigger.models.config import Config


@dataclass(frozen=True)
class WorkerContext:
    """All shared state passed to every worker process.

    Uses a frozen dataclass instead of Pydantic because it holds mp.Queue
    and mp.synchronize.Event — opaque OS-level objects that Pydantic cannot
    meaningfully validate.

    Queues are typed as Queue[Any] because each queue carries a union of
    message types (Task | ShutdownSentinel, TaskResult | TaskStarted, etc.)
    that cross the pickle boundary and lose static type information.

    Allowed members: mp.Queue, mp.synchronize.Event, Config, Path.
    Forbidden: logging.Logger (build it inside each process via
               build_worker_logger()); rich.Console (owns the terminal,
               must stay in the coordinator).

    All members must be pickle-safe for Windows multiprocessing spawn.

    temp_base is the root directory for per-task temp workspaces created by
    ArchiveMemberItem.materialize().  run_scan() creates a piidigger-prefixed
    directory and adds it to exclude_dirs so ENUM_DIR never scans it.
    The default (system temp dir) is safe for tests that do not scan archives.
    """

    config: Config
    task_queue: mp.Queue[Any]
    result_queue: mp.Queue[Any]
    log_queue: mp.Queue[Any]
    stop_event: mp.synchronize.Event
    temp_base: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
