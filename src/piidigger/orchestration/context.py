from __future__ import annotations

import multiprocessing as mp
import multiprocessing.synchronize
import os
import tempfile
from dataclasses import dataclass, field
from functools import cached_property
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

    temp_base is the root directory for per-task temp workspaces used by
    archive member extraction (handle_scan_archive_members() -> extract_members()).
    run_scan() creates a piidigger-prefixed directory and adds it to
    exclude_dirs so ENUM_DIR never scans it.
    The default (system temp dir) is safe for tests that do not scan archives.
    Each task run gets its own folder under it; see task_workspace().

    exclude_files holds the resolved, normcased paths of the output files this
    run is writing, which ENUM_DIR must skip.  run_scan() fills it only when an
    output folder is too broad to exclude as a whole (the cwd, a drive root).

    n_workers is the size of the worker pool.  Archive enumeration splits each
    archive into at least this many batches so every worker gets a share.
    """

    config: Config
    task_queue: mp.Queue[Any]
    result_queue: mp.Queue[Any]
    log_queue: mp.Queue[Any]
    stop_event: mp.synchronize.Event
    temp_base: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    n_workers: int = 1
    exclude_files: frozenset[str] = frozenset()

    @cached_property
    def exclude_file_names(self) -> frozenset[str]:
        """Normcased basenames of exclude_files, so ENUM_DIR can rule out most files by name alone.

        Computed once per process: cached_property stores the value in the
        instance __dict__, which a frozen dataclass still allows.
        """
        return frozenset(os.path.basename(f) for f in self.exclude_files)

    def task_workspace(self, task_id: str) -> Path:
        """Return the temp folder for one run of a task in the calling worker.

        Keyed by pid as well as task id, because task ids are reused across
        retries.  The lost-task sweep can re-dispatch a task whose first worker
        is only slow, so two live workers may run the same task at once.  With a
        shared folder, one would delete members the other is still scanning.
        One worker runs one task at a time and cleans up before the next, so the
        pid keeps live copies apart.
        """
        return self.temp_base / f"{task_id}-{os.getpid()}"
