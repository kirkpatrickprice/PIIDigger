from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from piidigger.exceptions import ArchiveReadError
from piidigger.models.config import Config
from piidigger.models.payloads import ScanArchiveMembersPayload
from piidigger.models.results import ResultRecord
from piidigger.models.tasks import Task, TaskResult
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.secure_delete import secure_delete
from piidigger.orchestration.sources import FilesystemItem
from piidigger.orchestration.worker._reporter import ProgressReporter
from piidigger.protocols import DataHandler


def handle_scan_archive_members(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Scan one batch: the archive members in task.items, a contiguous run in archive order.

    The archive is opened once.  Its handler extracts the members one at a
    time into task_temp; each is scanned, securely deleted, and reported with a
    TaskProgress item_done before the next is extracted.  So only one member is
    ever on disk, and each member's findings reach the output as soon as it is
    done — a later member that hangs or crashes the worker cannot lose them.

    task.items is exactly the work to do.  A re-queued batch arrives with the
    members already reported, and the one that hung or crashed, taken out.

    A member that fails on its own (no file handler, an extraction filter, a
    reader error) is reported done with a failure count, and the batch goes on.
    The batch fails as a whole only when the archive cannot be read; the
    coordinator then counts the members it never heard about as failed.

    ResultRecord lineage fields (source_member_path, source_depth,
    source_container_type) are filled in for every finding, so archive findings
    are traceable in all output formats.
    """
    from piidigger.archivehandlers import get_handler  # lazy: avoid top-level import at spawn
    from piidigger.datahandlers import HANDLER_REGISTRY  # lazy: deferred past warning-capture setup

    payload = ScanArchiveMembersPayload(**task.payload)

    archive_handler = get_handler(payload.archive_type)
    if archive_handler is None:
        logger.warning("no archive handler for type %r (%s)", payload.archive_type, payload.archive_path)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=f"no archive handler for type={payload.archive_type!r}",
            worker_pid=os.getpid(),
        )

    if "all" in ctx.config.data_handlers:
        enabled_handlers = list(HANDLER_REGISTRY.values())
    else:
        enabled_handlers = [HANDLER_REGISTRY[name] for name in ctx.config.data_handlers if name in HANDLER_REGISTRY]

    task_temp = ctx.task_workspace(task.task_id)
    task_temp.mkdir(exist_ok=True)
    temp_root = task_temp.resolve()
    reporter = ProgressReporter(ctx.result_queue.put, task.task_id)
    reported: set[str] = set()

    def failed(member: str, reason: str) -> None:
        logger.error("error scanning archive member %s::%s: %s", payload.archive_path, member, reason)
        reported.add(member)
        reporter.item_done(member, [], {"files_scanned": 1, "tasks_failed": 1})

    def discard(member: str, path: Path) -> None:
        # The file is overwritten and removed with the rest of task_temp when
        # the task ends, so a failure here (a handle a file handler left open,
        # an antivirus lock) only delays the deletion.
        try:
            secure_delete(path)
        except OSError as exc:
            logger.warning(
                "could not delete extracted member %s::%s yet (%s); it is removed when the task ends",
                payload.archive_path,
                member,
                exc,
            )

    def extracted(member: str, path: Path) -> None:
        # Called from inside the archive handler, so it must never raise: an
        # exception here would surface as an archive read error and end the
        # whole batch over one member.
        if not path.resolve().is_relative_to(temp_root):
            # Never scan or delete a file outside this task's own folder.  An
            # archive handler bug that mapped a member name onto a host path
            # would otherwise wipe that host file.
            failed(member, f"extracted path {path} is outside the task folder")
            return
        try:
            findings, counters = _scan_member(member, path, payload, ctx.config, enabled_handlers)
        except Exception as exc:  # noqa: BLE001
            discard(member, path)
            failed(member, str(exc))
            return
        discard(member, path)
        reported.add(member)
        reporter.item_done(member, findings, counters)

    try:
        archive_handler.extract_members(
            payload.archive_path,
            task.items,
            task_temp,
            on_extracted=extracted,
            on_started=reporter.item_started,
            on_failed=failed,
            on_progress=reporter.alive,
        )
    except ArchiveReadError as exc:
        logger.error("error reading archive %s: %s", payload.archive_path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"archive_errors": 1},
            worker_pid=os.getpid(),
        )

    for member in task.items:
        if member not in reported:
            failed(member, "member not found in archive")

    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        worker_pid=os.getpid(),
    )


def _scan_member(
    member: str,
    path: Path,
    payload: ScanArchiveMembersPayload,
    config: Config,
    enabled_handlers: list[DataHandler],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Run the file handler and data handlers over one extracted member.

    Returns the member's findings (one ResultRecord dict per data handler that
    matched) and its counters.  Raises on any reader error; the caller reports
    the member as failed.
    """
    from piidigger.filehandlers import get_handler_for  # lazy: xlrd import triggers SyntaxWarning

    ext = Path(member).suffix
    file_handler = get_handler_for(ext, None)
    if file_handler is None:
        # Defensive: handle_enum_archive_members filters these out.
        raise ValueError(f"no file handler for ext={ext!r}")

    size = path.stat().st_size
    item = FilesystemItem(path, mime=None, archive_path=payload.archive_path, member_path=member)

    per_handler: dict[str, dict[str, set[str]]] = {}
    for content in file_handler.read(item, config):
        if not content:
            continue
        for dh in enabled_handlers:
            for match_type, values in dh.find_matches(content).items():
                if values:
                    per_handler.setdefault(dh.name, {}).setdefault(match_type, set()).update(values)

    findings = [
        ResultRecord(
            source_path=str(payload.archive_path),
            source_member_path=member,
            source_depth=payload.depth,
            source_container_type=payload.archive_type,
            handler=handler_name,
            matches={k: sorted(v) for k, v in match_dict.items()},
        ).model_dump()
        for handler_name, match_dict in per_handler.items()
    ]
    results_found = sum(len(values) for f in findings for values in f.get("matches", {}).values())
    return findings, {"files_scanned": 1, "bytes_scanned": size, "results_found": results_found}
