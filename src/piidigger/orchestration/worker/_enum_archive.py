from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from piidigger.archivehandlers import HANDLER_REGISTRY, get_handler
from piidigger.exceptions import ArchiveReadError
from piidigger.models.payloads import EnumArchiveMembersPayload
from piidigger.models.tasks import Task, TaskResult, TaskType
from piidigger.orchestration.context import WorkerContext

# Extensions of archive formats we recognise as nested archives (skip for now).
_NESTED_ARCHIVE_EXTS: frozenset[str] = frozenset(f".{k}" for k in HANDLER_REGISTRY)


def handle_enum_archive_members(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Enumerate one archive: produce SCAN_ARCHIVE_MEMBER tasks for accepted members.

    Safety checks applied per member (in order):
      1. Member count limit
      2. Path traversal (../ or absolute)
      3. Encryption flag
      4. Individual uncompressed size limit
      5. Compression ratio > 1000:1 (bomb heuristic)
      6. Running total uncompressed size limit
      7. Nested archive (deferred — skipped in milestone 1)
      8. No registered file handler for this extension

    Only members that pass all checks reach SCAN_ARCHIVE_MEMBER.  This keeps
    the approach consistent with handle_enum_dir, which filters by handler
    existence before emitting SCAN_FILE tasks.
    """
    from piidigger.filehandlers import get_handler_for  # lazy: xlrd import triggers SyntaxWarning

    payload = EnumArchiveMembersPayload(**task.payload)
    archive_path = payload.archive_path
    archive_type = payload.archive_type
    depth = payload.depth
    arc_cfg = ctx.config.archives

    new_tasks: list[dict[str, Any]] = []
    files_found = 0
    bytes_found = 0
    members_skipped = 0
    archive_errors = 0

    handler = get_handler(archive_type)
    if handler is None:
        logger.warning("no archive handler registered for type %r (%s)", archive_type, archive_path)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=f"no handler for archive_type={archive_type!r}",
            counters={"files_scanned": 1, "archive_errors": 1},
            worker_pid=os.getpid(),
        )

    try:
        member_list = handler.list_members(archive_path)
    except ArchiveReadError as exc:
        logger.warning("cannot read archive %s: %s", archive_path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"files_scanned": 1, "archive_errors": 1},
            worker_pid=os.getpid(),
        )

    max_member_bytes = arc_cfg.max_member_uncompressed_size_mb * 1024 * 1024
    max_total_bytes = arc_cfg.max_total_uncompressed_size_mb * 1024 * 1024
    total_uncompressed: int = 0

    for i, member in enumerate(member_list):
        member_name = member.name

        # Skip directory entries
        if member.is_dir:
            continue

        # 1. Member count limit — stop enumeration entirely
        if files_found >= arc_cfg.max_members:
            remaining = sum(1 for m in member_list[i:] if not m.is_dir)
            members_skipped += remaining
            logger.warning(
                "archive %s: member count limit (%d) reached; %d member(s) not scanned",
                archive_path,
                arc_cfg.max_members,
                remaining,
            )
            break

        uncompressed_size = member.uncompressed_size
        compressed_size = member.compressed_size
        ext = Path(member_name).suffix

        # 2. Path traversal
        parts = member_name.replace("\\", "/").split("/")
        if ".." in parts or member_name.startswith("/"):
            logger.warning("archive %s: path traversal rejected for member %r", archive_path, member_name)
            members_skipped += 1
            archive_errors += 1
            continue

        # 3. Encryption
        if member.is_encrypted:
            logger.warning("archive %s: encrypted member skipped: %r", archive_path, member_name)
            members_skipped += 1
            continue

        # 4. Individual size limit
        if uncompressed_size > max_member_bytes:
            logger.warning(
                "archive %s: member %r exceeds size limit (%d MB > %d MB), skipping",
                archive_path,
                member_name,
                uncompressed_size // (1024 * 1024),
                arc_cfg.max_member_uncompressed_size_mb,
            )
            members_skipped += 1
            continue

        # 5. Compression ratio bomb heuristic
        if compressed_size > 0 and uncompressed_size > compressed_size * 1000:
            logger.warning(
                "archive %s: member %r compression ratio %.0f:1 exceeds 1000:1, rejecting",
                archive_path,
                member_name,
                uncompressed_size / compressed_size,
            )
            members_skipped += 1
            archive_errors += 1
            continue

        # 6. Running total size limit
        candidate_total = total_uncompressed + uncompressed_size
        if candidate_total > max_total_bytes:
            logger.warning(
                "archive %s: total uncompressed size limit (%d MB) reached at member %r, skipping",
                archive_path,
                arc_cfg.max_total_uncompressed_size_mb,
                member_name,
            )
            members_skipped += 1
            continue
        total_uncompressed = candidate_total

        # 7. Nested archive — deferred to a future milestone
        if ext.lower() in _NESTED_ARCHIVE_EXTS:
            logger.debug(
                "archive %s: nested archive member %r skipped (nested archives deferred to a future milestone)",
                archive_path,
                member_name,
            )
            members_skipped += 1
            continue

        # 8. Handler existence check (consistent with handle_enum_dir)
        if get_handler_for(ext, None) is None:
            logger.debug(
                "archive %s: no handler for member %r (ext=%r), skipping",
                archive_path,
                member_name,
                ext,
            )
            members_skipped += 1
            continue

        new_tasks.append(
            {
                "task_type": TaskType.SCAN_ARCHIVE_MEMBER,
                "payload": {
                    "archive_path": str(archive_path),
                    "member_path": member_name,
                    "archive_type": archive_type,
                    "ext": ext,
                    "mime": None,
                    "uncompressed_size": uncompressed_size,
                    "depth": depth + 1,
                },
                "timeout_seconds": ctx.config.default_timeout_seconds,
            }
        )
        files_found += 1
        bytes_found += uncompressed_size

    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        new_tasks=new_tasks,
        counters={
            "files_scanned": 1,
            "files_found": files_found,
            "bytes_found": bytes_found,
            "archive_members_skipped": members_skipped,
            "archive_errors": archive_errors,
        },
        worker_pid=os.getpid(),
    )
