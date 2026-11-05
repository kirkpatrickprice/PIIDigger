from __future__ import annotations

import logging
import os
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from piidigger.getmime import get_mime, test_magic
from piidigger.models.config import Config
from piidigger.models.payloads import EnumDirPayload
from piidigger.models.tasks import Task, TaskResult, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.counters import UNSUPPORTED_BY_TYPE
from piidigger.orchestration.worker._filesystems import is_remote_filesystem
from piidigger.orchestration.worker._removed import was_removed
from piidigger.orchestration.worker._reporter import ProgressReporter

# Attempts at listing a directory when a signal interrupts the read (EINTR).
_LIST_ATTEMPTS: int = 3

# A Recycle Bin $I file's name without its extension: "$I" and six characters.
_RECYCLE_STUB_STEM_LENGTH: int = 8


def _is_excluded(path: Path, exclude_dirs: list[str]) -> bool:
    """Return True if path matches any exclude pattern.

    Patterns that start with '*' are suffix-matched against the resolved path
    (e.g. '*/.vscode-server' matches any directory ending with that component).
    All others are exact- or prefix-matched against the resolved path.

    os.path.normcase() is applied to both sides so that on Windows, config
    entries written with forward slashes (e.g. 'C:/Program Files') correctly
    match the backslash paths returned by Path.resolve(), and case differences
    are tolerated.  On POSIX normcase is a no-op so behaviour is unchanged.
    """
    resolved = os.path.normcase(str(path.resolve()))
    for pattern in exclude_dirs:
        if pattern.startswith("*"):
            if resolved.endswith(os.path.normcase(pattern[1:])):
                return True
        else:
            norm = os.path.normcase(pattern)
            if resolved == norm or resolved.startswith(norm + os.sep):
                return True
    return False


def _is_excluded_file(path: Path, exclude_names: frozenset[str], exclude_files: frozenset[str]) -> bool:
    """Return True if path is one of exclude_files (resolved, normcased paths).

    exclude_names holds their normcased basenames.  Checking the name first
    means resolve() runs only for a likely match, not for every file scanned.
    """
    if os.path.normcase(path.name) not in exclude_names:
        return False
    return os.path.normcase(str(path.resolve())) in exclude_files


def resolve_exclude_dirs(exclude_dirs: list[str]) -> list[str]:
    """Return exclude_dirs with each absolute pattern resolved to its real path.

    _is_excluded() compares against entry.resolve(), so a pattern that reaches
    its target through a symlink (macOS /etc -> /private/etc, /var ->
    /private/var) or a Windows 8.3 short name (RANDYB~1) never matches.
    Resolving once here, before the scan starts, keeps the per-entry check
    cheap.

    - '*' suffix patterns and relative patterns are returned unchanged.
    - A bare drive ('G:') is treated as its root; os.path.realpath() would
      otherwise resolve it to the current directory on that drive.
    - Trailing separators are stripped (a root becomes 'G:' or '') so the
      prefix match in _is_excluded() still adds exactly one separator.
    - Patterns that do not exist resolve as far as possible and still apply.
    """
    seps = os.sep + (os.altsep or "")
    resolved: list[str] = []
    for pattern in exclude_dirs:
        candidate = pattern
        drive, rest = os.path.splitdrive(candidate)
        if drive and not rest:
            candidate = drive + os.sep
        if pattern.startswith("*") or not os.path.isabs(candidate):
            resolved.append(pattern)
            continue
        try:
            resolved.append(os.path.realpath(candidate).rstrip(seps))
        except OSError, ValueError:
            resolved.append(pattern)
    return resolved


def configured_archive_type(filename: str, config: Config) -> str | None:
    """Return the archive_type for filename, or None if not a configured archive format."""
    if not config.archives.enabled:
        return None
    from piidigger.archivehandlers import detect_archive_type  # noqa: PLC0415

    archive_type = detect_archive_type(filename)
    if archive_type is None:
        return None
    if "all" in config.archives.formats:
        return archive_type
    return archive_type if archive_type in {f.lower() for f in config.archives.formats} else None


def _is_cloud_placeholder(path: Path) -> bool:
    """Return True if path is a cloud-sync placeholder not yet downloaded locally.

    On macOS, checks the SF_DATALESS file flag, which File Provider sets on
    iCloud Drive, OneDrive and other CloudStorage files whose content is not on
    local disk.  Opening such a file blocks while macOS downloads it.  Also
    treats a sparse file with no blocks at all as a placeholder: OneDrive's own
    storage under ~/Library/Group Containers holds such files, which report
    their full size but read back as zeros, and are not marked SF_DATALESS.
    APFS-compressed files also report zero blocks, so those are kept.

    On Windows, checks file attribute bits:
      0x400000 (Recall)  — OneDrive file whose content is not on local disk.
      0x001000 (Offline) — Dropbox file whose content is not on local disk.

    A set bit means skip the file; the content would have to be downloaded on
    demand, which defeats the purpose of local-only scanning.

    Returns False on other platforms, when pywin32 is not installed, or when the
    attribute read fails — all treated as "assume local, scan it."

    Reference: https://superuser.com/questions/1718444/determining-if-a-onedrive-file-is-synced-locally-via-a-terminal
    """
    if sys.platform == "darwin":
        try:
            st = path.stat(follow_symlinks=False)
        except OSError:
            return False
        # Regular files only.  An APFS directory reports a size but no blocks,
        # so the sparse-file rule below would call it a placeholder.
        if not stat.S_ISREG(st.st_mode):
            return False
        if st.st_flags & stat.SF_DATALESS:
            return True
        return st.st_size > 0 and st.st_blocks == 0 and not (st.st_flags & stat.UF_COMPRESSED)
    recall_bit = 0x400000
    offline_bit = 0x001000
    if os.name != "nt":
        return False
    try:
        from win32api import GetFileAttributes

        attr = GetFileAttributes(str(path))
        return bool(attr & recall_bit) or bool(attr & offline_bit)
    except Exception:
        return False


def _is_junction_cycle(path: Path, start_dirs: list[Path]) -> bool:
    """Return True if path is a Windows junction to a folder this walk came through.

    Only symlinks are skipped otherwise, so following this one would list the same
    tree again at every level until the path grew too long.  Only the folders
    actually walked count, at or below a start directory, so nothing is lost: a
    junction to a folder above the start directory is followed, because that
    content is not reached any other way.
    """
    if not path.is_junction():
        return False
    try:
        target = path.resolve()
        walked = (parent for parent in path.parents if any(parent.is_relative_to(start) for start in start_dirs))
        return any(parent.resolve() == target for parent in walked)
    except OSError:
        return False


def _list_dir(path: Path) -> list[Path]:
    """List path, retrying when a signal interrupts the read.

    Seen in macOS app sandbox containers.  Without the retry the directory, and
    everything under it, is skipped.
    """
    for attempt in range(_LIST_ATTEMPTS):
        try:
            return list(path.iterdir())
        except InterruptedError:
            if attempt == _LIST_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")


def _is_recycle_bin_stub(path: Path) -> bool:
    """Return True for a Windows Recycle Bin $I file: the deleted file's name, size and date, in binary.

    Windows names one $I plus six characters, directly under a folder per user
    (<bin>/<SID>/$IABC123.txt).  Its partner $R file is the deleted file itself,
    and a deleted folder keeps its files' own names, so anything else starting
    with $I is the user's and is still scanned, because deleted PII is still PII.
    """
    return (
        path.name.startswith("$I")
        and len(path.stem) == _RECYCLE_STUB_STEM_LENGTH
        and path.parent.parent.name.casefold() == "$recycle.bin"
    )


def _is_junction_to_scanned_folder(path: Path, start_dirs: list[Path]) -> bool:
    """Return True if path is a Windows junction whose target this scan reaches another way.

    The legacy junctions in a profile (Application Data, My Documents) refuse to
    be listed, and their targets are scanned under their real names, so a refusal
    loses nothing.  A junction elsewhere, or a folder-mounted volume, may be the
    only way to its content, so its refusal stays a failure.
    """
    if not path.is_junction():
        return False
    try:
        target = path.resolve()
        return target != path and any(target.is_relative_to(start.resolve()) for start in start_dirs)
    except OSError:
        return False


def handle_enum_dir(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Enumerate one directory: produce ENUM_DIR tasks for subdirs and SCAN_FILE tasks for files."""
    from piidigger.filehandlers import (  # lazy: xlrd import triggers SyntaxWarning
        file_ext,
        mime_can_change_handler,
        select_handler,
    )

    payload = EnumDirPayload(**task.payload)
    path = payload.path

    new_tasks: list[dict[str, Any]] = []
    dirs_found = 0
    files_found = 0
    bytes_found = 0
    unsupported: Counter[str] = Counter()

    try:
        entries = _list_dir(path)
    except PermissionError as exc:
        if _is_junction_to_scanned_folder(path, ctx.config.start_dirs):
            logger.info("skipping %s: junction refused listing, and its target is scanned directly", path)
            return TaskResult(
                task_id=task.task_id,
                task_type=task.task_type,
                status="ok",
                counters={"dirs_not_scanned": 1},
                worker_pid=os.getpid(),
            )
        # The coordinator logs the failure; worker lines would duplicate it.
        logger.debug("permission denied listing %s: %s", path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"dirs_not_scanned": 1},
            worker_pid=os.getpid(),
        )
    except FileNotFoundError as exc:
        # A start directory that does not exist is a configuration error, and
        # one that cannot be reached (a path too long on Windows) is a failure.
        # Only a folder that really was removed after it was listed is not.
        # payload.depth cannot tell a start directory apart: it is the archive
        # nesting depth, 0 for everything on disk.
        if path in ctx.config.start_dirs or not was_removed(path):
            logger.debug("cannot list %s: %s", path, exc)
            return TaskResult(
                task_id=task.task_id,
                task_type=task.task_type,
                status="error",
                error_message=str(exc),
                counters={"dirs_not_scanned": 1},
                worker_pid=os.getpid(),
            )
        # Removed after its parent was listed, e.g. by a software update: there
        # is nothing to scan, and it is not a failure.
        logger.info("skipping %s: folder no longer exists (removed after it was listed)", path)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="ok",
            counters={"dirs_not_scanned": 1},
            worker_pid=os.getpid(),
        )
    except OSError as exc:
        logger.debug("cannot list %s: %s", path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"dirs_not_scanned": 1},
            worker_pid=os.getpid(),
        )

    config = ctx.config
    # A heartbeat per entry (sent at most once a second): MIME detection makes
    # a directory of tens of thousands of files take minutes, and a listing
    # that keeps making progress must not be killed at its deadline.
    reporter = ProgressReporter(ctx.result_queue.put, task.task_id)
    for entry in entries:
        reporter.alive()
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if _is_excluded(entry, config.exclude_dirs):
                    continue
                if _is_junction_cycle(entry, config.start_dirs):
                    logger.info("skipping %s: junction points back at a folder already walked", entry)
                    continue
                if config.local_files_only and is_remote_filesystem(str(entry), entry.stat().st_dev):
                    logger.info("skipping %s: network filesystem (local_files_only)", entry)
                    continue
                new_tasks.append(
                    {
                        "task_type": TaskType.ENUM_DIR,
                        "payload": {"path": str(entry), "depth": payload.depth},
                        "timeout_seconds": config.default_timeout_seconds,
                    }
                )
                dirs_found += 1
            elif entry.is_file():
                if _is_excluded_file(entry, ctx.exclude_file_names, ctx.exclude_files):
                    continue
                if config.local_files_only and _is_cloud_placeholder(entry):
                    continue
                if _is_recycle_bin_stub(entry):
                    continue
                ext = file_ext(entry.name)

                # Archive files are enumerated separately — emit before the regular
                # handler check so they are not silently skipped (no FileHandler
                # is registered for .zip, .tar.gz, etc.).
                archive_type = configured_archive_type(entry.name, config)
                if archive_type is not None:
                    new_tasks.append(
                        {
                            "task_type": TaskType.ENUM_ARCHIVE_MEMBERS,
                            "payload": {
                                "archive_path": str(entry),
                                "archive_type": archive_type,
                                "depth": 0,
                            },
                            "timeout_seconds": config.default_timeout_seconds,
                        }
                    )
                    files_found += 1
                    # bytes_found for archive members is counted in _enum_archive.py
                    # using uncompressed member sizes — not the compressed on-disk size here.
                    continue

                mime: str | None = get_mime(str(entry)) if test_magic() and mime_can_change_handler(ext) else None

                # include_exts / include_mime, and a handler to read it with.
                # The same rule is applied to archive members.
                if select_handler(ext, mime, config.include_exts, config.include_mime) is None:
                    unsupported[ext.lower() or "(no extension)"] += 1
                    continue

                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0

                new_tasks.append(
                    {
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
                    }
                )
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
            **{f"{UNSUPPORTED_BY_TYPE}{kind}": count for kind, count in unsupported.items()},
        },
        worker_pid=os.getpid(),
    )
