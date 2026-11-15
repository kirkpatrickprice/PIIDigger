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
from piidigger.exceptions import ArchivePartlyReadError, ArchiveReadError
from piidigger.models.archive import MemberInfo
from piidigger.models.payloads import EnumArchiveMembersPayload
from piidigger.models.tasks import Task, TaskResult, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.counters import FILES_MACOS_METADATA, type_tally_text
from piidigger.orchestration.worker._error_kind import error_kind_of
from piidigger.orchestration.worker._not_content import (
    appledouble_original,
    comparable_name,
    in_macos_folder,
    not_content_counters,
    not_content_reason,
)
from piidigger.orchestration.worker._reporter import ProgressReporter

# Extensions of archive formats we recognise as nested archives (skip for now).
_NESTED_ARCHIVE_EXTS: frozenset[str] = frozenset(f".{k}" for k in HANDLER_REGISTRY)

# A member name that starts with a Windows drive letter ("C:", "c:/...").
_DRIVE_PREFIX: re.Pattern[str] = re.compile(r"^[A-Za-z]:")

# How many metadata members of one accepted original are not charged to
# max_members.  A real archive has at most two: "._name" beside it, and one
# under "__MACOSX/".  The rides go to the first ones accepted, in archive order.
_FREE_RIDES_PER_ORIGINAL: int = 2

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
    counter: str | None = None  # the summary counter these members add to, if any
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
         sign of a hostile archive, so it counts as unsafe
      2. Nested archive (deferred — skipped in milestone 1)
      3. include_exts and a file handler for the extension (case-insensitive),
         by the same rule as files on disk; see filehandlers.select_handler()
      4. Repeated path
      5. Encryption flag
      6. Individual uncompressed size limit
      7. Compression ratio > 1000:1 (bomb heuristic)
      8. How deep in the archive the member sits (its decompress_offset),
         against max_decompress_depth_mb
      9. Running total uncompressed size limit
     10. Member count limit

    A member named "._name" whose original "name" is also in the archive is
    macOS metadata in shape (AppleDouble).  Its name alone never decides that
    it is skipped.  It follows its original instead:
      - It is considered after every other member, so it can never crowd one
        out of max_total_uncompressed_size_mb or max_members.
      - When its type is one nothing reads (a nested archive, or no file
        handler), it is counted as macOS metadata, not as an unsupported or
        nested member.  Its original has the same type, so nothing readable is
        lost; the name decides only which count it goes in.
      - Otherwise it goes through checks 2 to 10 like any other member, and is
        not charged to max_members when its original was accepted.  The batch
        scan then checks its content once it is extracted (see
        _not_content.py), so a user's own "._notes.txt" is still read.

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
    from piidigger.filehandlers import file_ext, select_handler  # lazy: xlrd import triggers SyntaxWarning

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
            counters={"files_not_scanned": 1},
            worker_pid=os.getpid(),
        )

    # An archive on disk can itself be macOS metadata (._backup.zip beside
    # backup.zip), which no archive handler can list.
    if (reason := not_content_reason(archive_path)) is not None:
        logger.info("skipping %s: %s", archive_path, reason)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="ok",
            counters=not_content_counters(reason, None),
            worker_pid=os.getpid(),
        )

    reporter = ProgressReporter(ctx.result_queue.put, task.task_id)
    read_error: str | None = None
    try:
        member_list = handler.list_members(archive_path, reporter.alive)
    except ArchivePartlyReadError as exc:
        # Damage part way through: scan what could be listed, and fail the task
        # so the archive still counts as not fully scanned.
        member_list = exc.members
        read_error = str(exc)
    except ArchiveReadError as exc:
        # The coordinator logs the failure; a worker line would duplicate it.
        logger.debug("cannot read archive %s: %s", archive_path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            error_kind=error_kind_of(exc, path=archive_path),
            counters={"files_not_scanned": 1},
            worker_pid=os.getpid(),
        )

    max_member_bytes = arc_cfg.max_member_uncompressed_size_mb * 1024 * 1024
    max_total_bytes = arc_cfg.max_total_uncompressed_size_mb * 1024 * 1024
    max_depth_bytes = arc_cfg.max_decompress_depth_mb * 1024 * 1024
    total_mb = arc_cfg.max_total_uncompressed_size_mb
    total_uncompressed: int = 0
    seen: set[str] = set()
    not_selected: Counter[str] = Counter()

    traversal = _SkipTally(logging.WARNING, "path traversal rejected", "archive_members_unsafe")
    nested = _SkipTally(
        logging.INFO, "nested archive (nested archives are deferred to a future milestone)", "archive_members_nested"
    )
    repeated = _SkipTally(
        logging.WARNING,
        "path appears earlier in the archive; only its first copy is considered",
        "archive_members_repeated",
    )
    encrypted = _SkipTally(logging.WARNING, "encrypted", "archive_members_encrypted")
    oversize = _SkipTally(
        logging.WARNING,
        f"larger than max_member_uncompressed_size_mb ({arc_cfg.max_member_uncompressed_size_mb:,} MB)",
        "archive_members_limited",
    )
    bomb = _SkipTally(
        logging.WARNING, "compression ratio over 1000:1, rejected as a likely archive bomb", "archive_members_unsafe"
    )
    too_deep = _SkipTally(
        logging.WARNING,
        f"reaching them means decompressing more than max_decompress_depth_mb ({arc_cfg.max_decompress_depth_mb:,} MB)",
        "archive_members_too_deep",
    )
    over_total = _SkipTally(
        logging.WARNING, f"max_total_uncompressed_size_mb ({total_mb:,} MB) reached", "archive_members_over_total"
    )
    over_count = _SkipTally(
        logging.WARNING, f"max_members ({arc_cfg.max_members:,}) reached", "archive_members_limited"
    )
    tallies = [traversal, nested, repeated, encrypted, oversize, bomb, too_deep, over_total, over_count]

    macos = _SkipTally(logging.INFO, "macOS metadata, of a type nothing reads", FILES_MACOS_METADATA)
    tallies.append(macos)

    # A "._name" member is macOS metadata in shape when its original "name" is
    # in the archive, or when it sits in Finder's top-level "__MACOSX/" folder
    # (see appledouble_original()).  It follows its original: it is considered
    # only after every other member, so it can never crowd one out, and it is
    # not charged to max_members when its original was accepted.  wanted holds
    # the names the "._" members point at, and originals those of them that are
    # members (a file or a folder entry).  Both stay empty in an archive with no
    # "._" member, so they never grow with the archive alone.
    wanted = {
        original
        for member in member_list
        if (original := appledouble_original(comparable_name(member.name))) is not None
    }
    originals = {name for member in member_list if (name := comparable_name(member.name).rstrip("/")) in wanted}
    outcomes: dict[str, bool] = {}  # each wanted original: whether any copy of it was accepted
    pending: list[tuple[int, MemberInfo, str]] = []
    accepted_at: list[tuple[int, str, int]] = []
    counted = 0  # members charged to max_members

    def consider(index: int, member: MemberInfo, *, metadata: bool, charged: bool) -> bool:
        """Apply checks 2 to 10 to one member, and return whether it is accepted.

        A metadata member of a type nothing reads (check 2 or 3) is counted as
        macOS metadata: nothing could read it anyway, so its name decides only
        which count it goes in.  Any other reason it is not read is reported
        under that reason, since it might be the user's own file.
        """
        nonlocal total_uncompressed, counted, files_found, bytes_found
        member_name = member.name
        uncompressed_size = member.uncompressed_size
        compressed_size = member.compressed_size
        ext = file_ext(member_name)

        def reject(tally: _SkipTally) -> bool:
            tally.add(member_name)
            return False

        # 2. Nested archive — deferred to a future milestone
        if ext.lower() in _NESTED_ARCHIVE_EXTS:
            return reject(macos if metadata else nested)

        # 3. include_exts / include_mime, and a handler to read it with — the
        # same rule handle_enum_dir applies to files on disk.  Members have no
        # detected MIME type, so only their extension can qualify them.
        if select_handler(ext, None, ctx.config.include_exts, ctx.config.include_mime) is None:
            if metadata:
                return reject(macos)
            not_selected[ext.lower() or "(no extension)"] += 1
            return False

        # 4. A batch names its members by path, so only the first copy of a
        # path is considered.
        if member_name in seen:
            return reject(repeated)
        seen.add(member_name)

        # 5. Encryption
        if member.is_encrypted:
            return reject(encrypted)

        # 6. Individual size limit
        if uncompressed_size > max_member_bytes:
            return reject(oversize)

        # 7. Compression ratio bomb heuristic
        if compressed_size > 0 and uncompressed_size > compressed_size * 1000:
            return reject(bomb)

        # 8. How deep the member sits: in a compressed tar or solid 7z,
        # reaching it means decompressing every byte stored before it, skipped
        # members included.  This limit controls time, where the running total
        # below controls how much is scanned.  The member's own size has its own
        # limit, in check 6.
        if member.decompress_offset > max_depth_bytes:
            return reject(too_deep)

        # 9. Running total size limit, over scannable members only.
        candidate_total = total_uncompressed + uncompressed_size
        if candidate_total > max_total_bytes:
            return reject(over_total)

        # 10. Member count limit, last, so it counts only members that would
        # otherwise be scanned.  Up to two metadata members per accepted
        # original are not charged (see the loop below).
        if charged and counted >= arc_cfg.max_members:
            return reject(over_count)

        total_uncompressed = candidate_total
        accepted_at.append((index, member_name, uncompressed_size))
        counted += charged
        files_found += 1
        bytes_found += uncompressed_size
        return True

    for index, member in enumerate(member_list):
        # Skip directory entries
        if member.is_dir:
            continue

        # 1. Path traversal.  Normalise backslashes first: on Windows a name
        # like "\x\y.txt" is rooted at the drive, and "C:x.txt" names a drive.
        normalized = comparable_name(member.name)
        parts = normalized.split("/")
        if ".." in parts or normalized.startswith("/") or _DRIVE_PREFIX.match(normalized):
            traversal.add(member.name)
            continue

        original = appledouble_original(normalized)
        if original is not None and (original in originals or in_macos_folder(normalized)):
            pending.append((index, member, original))
            continue
        was_accepted = consider(index, member, metadata=False, charged=True)
        if normalized in wanted:
            # Accepted if any spelling of it was ("a.txt" encrypted, "./a.txt" not).
            outcomes[normalized] = outcomes.get(normalized, False) or was_accepted

    # A real original has at most two metadata members: "._name" beside it,
    # and one under "__MACOSX/".  Only that many ride free, the first ones
    # accepted in archive order.  Any more are charged like any member, so a
    # hostile archive cannot push work past max_members with other spellings
    # of the same names ("./._a", ".\\._a").  In an unusual archive that holds
    # two such spellings, the "__MACOSX/" member may be charged; it is still
    # read, or reported under its own reason.  A ride is used up only by a
    # member that is accepted, so one rejected for its own size costs nothing.
    free_rides: Counter[str] = Counter()
    for index, member, original in pending:
        free = outcomes.get(original, False) and free_rides[original] < _FREE_RIDES_PER_ORIGINAL
        if consider(index, member, metadata=True, charged=not free) and free:
            free_rides[original] += 1

    accepted = [(name, size) for _, name, size in sorted(accepted_at)]

    if not_selected:
        # One line per archive rather than one per member: an archive of
        # thousands of images would otherwise flood the log.
        logger.info(
            "archive %s: %d member(s) not scanned, by type (no file handler, or excluded by include_exts): %s",
            archive_path,
            sum(not_selected.values()),
            type_tally_text(not_selected),
        )
    for tally in tallies:
        tally.report(logger, archive_path)

    skip_counters: Counter[str] = Counter()
    for tally in tallies:
        if tally.counter is not None and tally.count:
            skip_counters[tally.counter] += tally.count
    if not_selected:
        skip_counters["archive_members_unsupported"] = sum(not_selected.values())

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
        status="error" if read_error else "ok",
        error_message=read_error,
        new_tasks=new_tasks,
        counters={
            "archives_opened": 1,
            # Metadata counted by name is found and done in one step.
            "files_found": files_found + macos.count,
            "bytes_found": bytes_found,
            **skip_counters,
        },
        worker_pid=os.getpid(),
    )
