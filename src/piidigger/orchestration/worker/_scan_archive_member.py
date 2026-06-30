from __future__ import annotations

import logging
import os
from typing import Any

from piidigger.exceptions import ArchiveReadError
from piidigger.models.payloads import ScanArchiveMemberPayload
from piidigger.models.results import ResultRecord
from piidigger.models.tasks import Task, TaskResult
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.sources import FilesystemItem


def handle_scan_archive_member(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Scan one archive member: extract to task_temp, wrap as FilesystemItem, run handlers.

    Extraction uses the registered ArchiveHandler for the member's format.
    The extracted file lives in task_temp for the duration of this task and is
    securely deleted by _cleanup_temp_workspace() in the worker loop.

    ResultRecord lineage fields (source_member_path, source_depth,
    source_container_type) are populated from the payload so archive findings
    are traceable in all output formats.
    """
    from piidigger.archivehandlers import get_handler  # lazy: avoid top-level import at spawn
    from piidigger.datahandlers import HANDLER_REGISTRY  # lazy: deferred past warning-capture setup
    from piidigger.filehandlers import get_handler_for  # lazy: xlrd import triggers SyntaxWarning

    payload = ScanArchiveMemberPayload(**task.payload)

    # Resolve archive handler first — if it's missing we can't extract anything.
    archive_handler = get_handler(payload.archive_type)
    if archive_handler is None:
        logger.warning(
            "no archive handler for type %r (member %s::%s)",
            payload.archive_type,
            payload.archive_path,
            payload.member_path,
        )
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=f"no archive handler for type={payload.archive_type!r}",
            counters={"files_scanned": 1},
            worker_pid=os.getpid(),
        )

    file_handler = get_handler_for(payload.ext, payload.mime)
    if file_handler is None:
        # Defensive: handle_enum_archive_members should have filtered this out
        logger.warning(
            "no file handler for archive member %s::%s (ext=%r mime=%r)",
            payload.archive_path,
            payload.member_path,
            payload.ext,
            payload.mime,
        )
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=f"no handler for ext={payload.ext!r}",
            counters={"files_scanned": 1},
            worker_pid=os.getpid(),
        )

    if "all" in ctx.config.data_handlers:
        enabled_handlers = list(HANDLER_REGISTRY.values())
    else:
        enabled_handlers = [
            HANDLER_REGISTRY[name]
            for name in ctx.config.data_handlers
            if name in HANDLER_REGISTRY
        ]

    task_temp = ctx.temp_base / task.task_id
    task_temp.mkdir(exist_ok=True)

    try:
        extracted_path = archive_handler.extract_member(
            payload.archive_path, payload.member_path, task_temp
        )
    except ArchiveReadError as exc:
        logger.error(
            "error extracting %s::%s: %s",
            payload.archive_path,
            payload.member_path,
            exc,
        )
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"files_scanned": 1},
            worker_pid=os.getpid(),
        )

    item = FilesystemItem(
        extracted_path,
        mime=payload.mime,
        archive_path=payload.archive_path,
        member_path=payload.member_path,
    )

    per_handler: dict[str, dict[str, set[str]]] = {}

    try:
        for chunk in file_handler.read(item):
            if not chunk:
                continue
            for dh in enabled_handlers:
                matches = dh.find_matches(chunk)
                for match_type, values in matches.items():
                    if values:
                        bucket = per_handler.setdefault(dh.name, {})
                        bucket.setdefault(match_type, set()).update(values)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "error reading archive member %s::%s: %s",
            payload.archive_path,
            payload.member_path,
            exc,
        )
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"files_scanned": 1, "bytes_scanned": payload.uncompressed_size},
            worker_pid=os.getpid(),
        )

    findings: list[dict[str, Any]] = []
    for handler_name, match_dict in per_handler.items():
        record = ResultRecord(
            source_path=str(payload.archive_path),
            source_member_path=payload.member_path,
            source_depth=payload.depth,
            source_container_type=payload.archive_type,
            handler=handler_name,
            matches={k: sorted(v) for k, v in match_dict.items()},
        )
        findings.append(record.model_dump())

    results_count = sum(len(v) for f in findings for v in f.get("matches", {}).values())
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        findings=findings,
        counters={
            "files_scanned": 1,
            "bytes_scanned": payload.uncompressed_size,
            "results_found": results_count,
        },
        worker_pid=os.getpid(),
    )
