from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
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

# How many member names a skip report lists before "and N more".
_EXAMPLE_NAMES: int = 3


@dataclass(slots=True)
class _SkipTally:
    """Members of one archive skipped for one reason, reported in a single log line.

    One line per reason rather than one per member: a hostile archive can hold
    millions of entries, and every line passes through the worker's log queue.
    Only the first few names are kept, so memory does not grow with the archive.

    A dataclass: every field is a value this module sets.  Not frozen, because
    add() updates it in place.
    """

    level: int
    reason: str
    count: int = 0
    examples: list[str] = field(default_factory=list)

    def add(self, name: str) -> None:
        self.count += 1
        if len(self.examples) < _EXAMPLE_NAMES:
            self.examples.append(name)

    def report(self, logger: logging.Logger, archive_path: Path) -> None:
        if not self.count:
            return
        names = ", ".join(repr(name) for name in self.examples)
        if self.count > len(self.examples):
            names += f" and {self.count - len(self.examples):,} more"
        logger.log(
            self.level, "archive %s: %d member(s) not scanned: %s (%s)", archive_path, self.count, self.reason, names
        )


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

    Checks applied per member (in order):
      1. Path traversal (../ or absolute), whatever the member's type: it is a
         sign of a hostile archive, so it counts as an archive error
      2. Nested archive (deferred — skipped in milestone 1)
      3. include_exts and a file handler for the extension (case-insensitive),
         by the same rule as files on disk; see filehandlers.select_handler()
      4. Repeated path
      5. Encryption flag
      6. Individual uncompressed size limit
      7. Compression ratio > 1000:1 (bomb heuristic)
      8. How deep in the archive the member sits (its decompress_offset),
         against the total size limit
      9. Running total uncompressed size limit
     10. Member count limit

    Members that could never be scanned are filtered out by checks 2 and 3,
    before the limits.  So they do not use up the member count or the size
    budget, and cannot crowd out scannable members later in the archive.  Check 8
    is the exception by design: unscannable members stored ahead of a member
    still have to be decompressed to reach it.  The member count comes last,
    so it counts only members that would otherwise be scanned.

    Every member is checked, including those past the member count limit, so
    each skip reason is logged once per archive with a few example names (see
    _SkipTally), never once per member.

    A path that appears twice in one archive is enumerated once, at its first
    occurrence, because a batch names its members by path.  Later copies are
    skipped whether or not the first copy passed the other checks: extraction
    takes the first entry for a name, so a later copy could never be scanned.

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
        # The coordinator logs the failure; a worker line would duplicate it.
        logger.debug("cannot read archive %s: %s", archive_path, exc)
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
    total_mb = arc_cfg.max_total_uncompressed_size_mb
    total_uncompressed: int = 0
    accepted: list[tuple[str, int]] = []
    seen: set[str] = set()
    not_selected: Counter[str] = Counter()

    traversal = _SkipTally(logging.WARNING, "path traversal rejected")
    nested = _SkipTally(logging.DEBUG, "nested archive (nested archives are deferred to a future milestone)")
    repeated = _SkipTally(logging.WARNING, "path appears earlier in the archive; only its first copy is considered")
    encrypted = _SkipTally(logging.WARNING, "encrypted")
    oversize = _SkipTally(
        logging.WARNING,
        f"larger than max_member_uncompressed_size_mb ({arc_cfg.max_member_uncompressed_size_mb:,} MB)",
    )
    bomb = _SkipTally(logging.WARNING, "compression ratio over 1000:1, rejected as a likely archive bomb")
    too_deep = _SkipTally(
        logging.WARNING, f"reaching them means decompressing more than max_total_uncompressed_size_mb ({total_mb:,} MB)"
    )
    over_total = _SkipTally(logging.WARNING, f"max_total_uncompressed_size_mb ({total_mb:,} MB) reached")
    over_count = _SkipTally(logging.WARNING, f"max_members ({arc_cfg.max_members:,}) reached")
    tallies = [traversal, nested, repeated, encrypted, oversize, bomb, too_deep, over_total, over_count]

    for member in member_list:
        member_name = member.name

        # Skip directory entries
        if member.is_dir:
            continue

        uncompressed_size = member.uncompressed_size
        compressed_size = member.compressed_size
        ext = Path(member_name).suffix

        # 1. Path traversal.  Normalise backslashes first: on Windows a name
        # like "\x\y.txt" is rooted at the drive, and "C:x.txt" names a drive.
        normalized = member_name.replace("\\", "/")
        parts = normalized.split("/")
        if ".." in parts or normalized.startswith("/") or _DRIVE_PREFIX.match(normalized):
            traversal.add(member_name)
            continue

        # 2. Nested archive — deferred to a future milestone
        if ext.lower() in _NESTED_ARCHIVE_EXTS:
            nested.add(member_name)
            continue

        # 3. include_exts / include_mime, and a handler to read it with — the
        # same rule handle_enum_dir applies to files on disk.  Members have no
        # detected MIME type, so only their extension can qualify them.
        if select_handler(ext, None, ctx.config.include_exts, ctx.config.include_mime) is None:
            not_selected[ext.lower() or "(no extension)"] += 1
            continue

        # 4. A batch names its members by path, so only the first copy of a
        # path is considered.
        if member_name in seen:
            repeated.add(member_name)
            continue
        seen.add(member_name)

        # 5. Encryption
        if member.is_encrypted:
            encrypted.add(member_name)
            continue

        # 6. Individual size limit
        if uncompressed_size > max_member_bytes:
            oversize.add(member_name)
            continue

        # 7. Compression ratio bomb heuristic
        if compressed_size > 0 and uncompressed_size > compressed_size * 1000:
            bomb.add(member_name)
            continue

        # 8. How deep the member sits: in a compressed tar or solid 7z,
        # reaching it means decompressing every byte stored before it, skipped
        # members included.  Capped by the same limit as the running total.
        if member.decompress_offset + uncompressed_size > max_total_bytes:
            too_deep.add(member_name)
            continue

        # 9. Running total size limit, over scannable members only.
        candidate_total = total_uncompressed + uncompressed_size
        if candidate_total > max_total_bytes:
            over_total.add(member_name)
            continue

        # 10. Member count limit, last, so it counts only members that would
        # otherwise be scanned.
        if files_found >= arc_cfg.max_members:
            over_count.add(member_name)
            continue

        total_uncompressed = candidate_total
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
    for tally in tallies:
        tally.report(logger, archive_path)

    members_skipped = sum(not_selected.values()) + sum(tally.count for tally in tallies)
    archive_errors = traversal.count + bomb.count

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
