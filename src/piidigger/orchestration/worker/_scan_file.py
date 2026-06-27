from __future__ import annotations

import logging
import os
from typing import Any

from piidigger.datahandlers import HANDLER_REGISTRY
from piidigger.filehandlers import get_handler_for
from piidigger.models.payloads import ScanFilePayload
from piidigger.models.results import ResultRecord
from piidigger.models.tasks import Task, TaskResult
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.sources import FilesystemItem


def handle_scan_file(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Scan one file: run all enabled data handlers over each text chunk."""
    payload = ScanFilePayload(**task.payload)

    file_handler = get_handler_for(payload.ext, payload.mime)
    if file_handler is None:
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=f"no file handler for ext={payload.ext!r} mime={payload.mime!r}",
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

    item = FilesystemItem(payload.file_path, mime=payload.mime)

    # Aggregate matches per data-handler across all chunks to produce one
    # ResultRecord per (file, handler) pair.
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
        logger.error("error reading %s: %s", payload.display_path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            error_message=str(exc),
            counters={"files_scanned": 1, "bytes_scanned": payload.size},
            worker_pid=os.getpid(),
        )

    findings: list[dict[str, Any]] = []
    for handler_name, match_dict in per_handler.items():
        record = ResultRecord(
            source_path=payload.display_path,
            handler=handler_name,
            matches={k: sorted(v) for k, v in match_dict.items()},
        )
        findings.append(record.model_dump())

    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        findings=findings,
        counters={"files_scanned": 1, "bytes_scanned": payload.size},
        worker_pid=os.getpid(),
    )
