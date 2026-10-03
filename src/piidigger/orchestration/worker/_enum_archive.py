from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from piidigger.archivehandlers import HANDLER_REGISTRY, get_handler
from piidigger.exceptions import ArchiveReadError
from piidigger.models.payloads import EnumArchiveMembersPayload
from piidigger.models.tasks import Task, TaskResult, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.worker._reporter import ProgressReporter

# Extensions of archive formats we recognise as nested archives (skip for now).
_NESTED_ARCHIVE_EXTS: frozenset[str] = frozenset(f".{k}" for k in HANDLER_REGISTRY)

# A member name that starts with a Windows drive letter ("C:", "c:/...").
_DRIVE_PREFIX: re.Pattern[str] = re.compile(r"^[A-Za-z]:")


def batch_count(n_members: int, total_bytes: int, n_workers: int, max_batch_bytes: int) -> int:
    """How many scan tasks to split an archive's accepted members into.

    At least one per worker, so the scanning (by far the slowest part) is
    shared.  More when that would leave a batch above max_batch_bytes.  Never
    more than one per member, which is where per-member tasks used to sit.
    Each batch beyond the first re-decompresses the part of the archive before
    it, so the count is kept as low as those two goals allow.
    """
    if n_members == 0:
        return 0
    return min(n_members, max(n_workers, math.ceil(total_bytes / max_batch_bytes)))


def split_into_batches(members: list[tuple[str, int]], k: int) -> list[list[str]]:
    """Split (name, size) pairs, in archive order, into at most k contiguous runs of similar total size.

    Each member goes to the run its byte midpoint falls in, so the runs stay
    contiguous and in order.  With no bytes at all (every member empty), the
    split is by count instead.  A run that ends up empty, because one member
    is larger than a whole share, is dropped.
    """
    if not members or k <= 0:
        return []
    total = sum(size for _, size in members)
    runs: list[list[str]] = [[] for _ in range(k)]
    before = 0
    for index, (name, size) in enumerate(members):
        if total:
            run = int((before + size / 2) * k / total)
        else:
            run = index * k // len(members)
        runs[min(run, k - 1)].append(name)
        before += size
    return [run for run in runs if run]


def handle_enum_archive_members(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Enumerate one archive: produce SCAN_ARCHIVE_MEMBERS batch tasks for accepted members.

    Safety checks applied per member (in order):
      1. Member count limit
      2. Path traversal (../ or absolute)
      3. Encryption flag
      4. Individual uncompressed size limit
      5. Compression ratio > 1000:1 (bomb heuristic)
      6. Running total uncompressed size limit, and how deep in the archive the
         member sits (its decompress_offset) against the same limit
      7. Nested archive (deferred — skipped in milestone 1)
      8. include_exts and a file handler for the extension (case-insensitive),
         by the same rule as files on disk; see filehandlers.select_handler()

    A path that appears twice in one archive is enumerated once, at its first
    occurrence, because a batch names its members by path.

    Accepted members are then split into contiguous runs in archive order, one
    SCAN_ARCHIVE_MEMBERS task per run (see batch_count).  This keeps the
    approach consistent with handle_enum_dir: enumeration decides what is
    scanned, and separate tasks scan it.
    """
    from piidigger.filehandlers import select_handler  # lazy: xlrd import triggers SyntaxWarning

    payload = EnumArchiveMembersPayload(**task.payload)
    archive_path = payload.archive_path
    archive_type = payload.archive_type
    depth = payload.depth
    arc_cfg = ctx.config.archives

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

    reporter = ProgressReporter(ctx.result_queue.put, task.task_id)
    try:
        member_list = handler.list_members(archive_path, reporter.alive)
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
    accepted: list[tuple[str, int]] = []
    seen: set[str] = set()
    too_deep = 0
    not_selected: Counter[str] = Counter()

    for i, member in enumerate(member_list):
        member_name = member.name

        # Skip directory entries
        if member.is_dir:
            continue

        # A batch names its members by path, so each path is scanned once.
        if member_name in seen:
            logger.warning(
                "archive %s: member %r appears more than once; scanning the first copy", archive_path, member_name
            )
            members_skipped += 1
            continue
        seen.add(member_name)

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

        # 2. Path traversal.  Normalise backslashes first: on Windows a name
        # like "\x\y.txt" is rooted at the drive, and "C:x.txt" names a drive.
        normalized = member_name.replace("\\", "/")
        parts = normalized.split("/")
        if ".." in parts or normalized.startswith("/") or _DRIVE_PREFIX.match(normalized):
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

        # 6. Running total size limit.  Also caps how deep the member sits: in
        # a compressed tar or solid 7z, reaching it means decompressing every
        # byte stored before it, skipped members included.
        if member.decompress_offset + uncompressed_size > max_total_bytes:
            too_deep += 1
            members_skipped += 1
            continue
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

        # 8. include_exts / include_mime, and a handler to read it with — the
        # same rule handle_enum_dir applies to files on disk.  Members have no
        # detected MIME type, so only their extension can qualify them.
        if select_handler(ext, None, ctx.config.include_exts, ctx.config.include_mime) is None:
            not_selected[ext.lower() or "(no extension)"] += 1
            members_skipped += 1
            continue

        accepted.append((member_name, uncompressed_size))
        files_found += 1
        bytes_found += uncompressed_size

    if not_selected:
        # One line per archive rather than one per member: an archive of
        # thousands of images would otherwise flood the log.
        logger.info(
            "archive %s: %d member(s) not scanned, by type (no file handler, or excluded by include_exts): %s",
            archive_path,
            sum(not_selected.values()),
            ", ".join(f"{ext} ×{count}" for ext, count in not_selected.most_common()),
        )

    if too_deep:
        logger.warning(
            "archive %s: %d member(s) skipped because reaching them means decompressing more than %d MB",
            archive_path,
            too_deep,
            arc_cfg.max_total_uncompressed_size_mb,
        )

    k = batch_count(len(accepted), bytes_found, ctx.n_workers, arc_cfg.max_batch_mb * 1024 * 1024)
    new_tasks: list[dict[str, Any]] = [
        {
            "task_type": TaskType.SCAN_ARCHIVE_MEMBERS,
            "payload": {
                "archive_path": str(archive_path),
                "archive_type": archive_type,
                "depth": depth + 1,
            },
            "items": batch,
            "timeout_seconds": ctx.config.default_timeout_seconds,
        }
        for batch in split_into_batches(accepted, k)
    ]

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
