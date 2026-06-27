from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from piidigger.filehandlers import get_handler_for
from piidigger.getmime import get_mime, test_magic
from piidigger.models.payloads import EnumDirPayload
from piidigger.models.tasks import Task, TaskResult, TaskType
from piidigger.orchestration.context import WorkerContext


def _is_excluded(path: Path, exclude_dirs: list[str]) -> bool:
    """Return True if path matches any exclude pattern.

    Patterns that start with '*' are treated as name-suffix/contains checks.
    All others are prefix-matched against the resolved path string.
    """
    resolved = str(path.resolve())
    name = path.name
    for pattern in exclude_dirs:
        if pattern.startswith("*"):
            suffix = pattern[1:]
            if resolved.endswith(suffix) or name == suffix.lstrip("/\\"):
                return True
        elif resolved == pattern or resolved.startswith(pattern + os.sep):
            return True
    return False


def _is_cloud_placeholder(path: Path) -> bool:
    """Return True if path is a cloud-sync placeholder not yet downloaded locally.

    Checks Windows file attribute bits:
      0x400000 (Recall)  — OneDrive file whose content is not on local disk.
      0x001000 (Offline) — Dropbox file whose content is not on local disk.

    A set bit means skip the file; the content would have to be downloaded on
    demand, which defeats the purpose of local-only scanning.

    Returns False on non-Windows, when pywin32 is not installed, or when the
    attribute read fails — all treated as "assume local, scan it."

    Reference: https://superuser.com/questions/1718444/determining-if-a-onedrive-file-is-synced-locally-via-a-terminal
    """
    recall_bit = 0x400000
    offline_bit = 0x001000
    try:
        from win32api import GetFileAttributes
        attr = GetFileAttributes(str(path))
        return bool(attr & recall_bit) or bool(attr & offline_bit)
    except Exception:
        return False


def handle_enum_dir(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Enumerate one directory: produce ENUM_DIR tasks for subdirs and SCAN_FILE tasks for files."""
    payload = EnumDirPayload(**task.payload)
    path = payload.path

    new_tasks: list[dict[str, Any]] = []
    dirs_found = 0
    files_found = 0
    bytes_found = 0

    try:
        entries = list(path.iterdir())
    except PermissionError as exc:
        logger.warning("permission denied listing %s: %s", path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"dirs_scanned": 1},
            worker_pid=os.getpid(),
        )
    except (OSError, FileNotFoundError) as exc:
        logger.warning("cannot list %s: %s", path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"dirs_scanned": 1},
            worker_pid=os.getpid(),
        )

    config = ctx.config
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if _is_excluded(entry, config.exclude_dirs):
                    continue
                new_tasks.append({
                    "task_type": TaskType.ENUM_DIR,
                    "payload": {"path": str(entry), "depth": payload.depth},
                    "timeout_seconds": config.default_timeout_seconds,
                })
                dirs_found += 1
            elif entry.is_file():
                if config.local_files_only and _is_cloud_placeholder(entry):
                    continue
                ext = entry.suffix
                mime: str | None = get_mime(str(entry)) if test_magic() else None

                # Filter by include_exts / include_mime
                include_all_exts = "all" in config.include_exts
                include_all_mime = "all" in config.include_mime
                ext_ok = include_all_exts or ext in config.include_exts
                mime_ok = include_all_mime or (mime is not None and mime in config.include_mime)

                if not (ext_ok or mime_ok):
                    continue
                if get_handler_for(ext, mime) is None:
                    continue

                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0

                new_tasks.append({
                    "task_type": TaskType.SCAN_FILE,
                    "payload": {
                        "display_path": str(entry),
                        "file_path": str(entry),
                        "ext": ext,
                        "mime": mime,
                        "size": size,
                        "depth": payload.depth,
                    },
                    "timeout_seconds": config.default_timeout_seconds,
                })
                files_found += 1
                bytes_found += size
        except OSError:
            continue

    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        new_tasks=new_tasks,
        counters={
            "dirs_scanned": 1,
            "dirs_found": dirs_found,
            "files_found": files_found,
            "bytes_found": bytes_found,
        },
        worker_pid=os.getpid(),
    )
