from __future__ import annotations

import logging
import os
from typing import Any

from piidigger.exceptions import UndetectableEncodingError
from piidigger.models.payloads import ScanFilePayload
from piidigger.models.results import ResultRecord
from piidigger.models.tasks import Task, TaskResult
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.sources import FilesystemItem
from piidigger.orchestration.worker._removed import was_removed
from piidigger.orchestration.worker._reporter import ProgressReporter


def handle_scan_file(task: Task, ctx: WorkerContext, logger: logging.Logger) -> TaskResult:
    """Scan one file: run all enabled data handlers over each piece of extracted content."""
    from piidigger.datahandlers import HANDLER_REGISTRY  # lazy: deferred past warning-capture setup
    from piidigger.filehandlers import get_handler_for  # lazy: xlrd import triggers SyntaxWarning

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
        enabled_handlers = [HANDLER_REGISTRY[name] for name in ctx.config.data_handlers if name in HANDLER_REGISTRY]

    # A large file can take longer than the task deadline.  Two signals keep it
    # alive without any file handler knowing: reads from disk through
    # open_stream(), and each piece of content the handler yields (for handlers
    # that read via materialize()).  A handler stuck with neither still times out.
    reporter = ProgressReporter(ctx.result_queue.put, task.task_id)
    item = FilesystemItem(payload.file_path, mime=payload.mime, on_progress=reporter.alive)

    # Aggregate matches per data-handler across all content pieces to produce one
    # ResultRecord per (file, handler) pair.
    per_handler: dict[str, dict[str, set[str]]] = {}

    try:
        for content in file_handler.read(item, ctx.config):
            reporter.alive()
            if not content:
                continue
            for dh in enabled_handlers:
                matches = dh.find_matches(content)
                for match_type, values in matches.items():
                    if values:
                        bucket = per_handler.setdefault(dh.name, {})
                        bucket.setdefault(match_type, set()).update(values)
    except UndetectableEncodingError as exc:
        # Usually binary content behind a text extension: not a failure, but
        # logged so an operator can see which files were never read.
        logger.info("skipping %s: %s", payload.display_path, exc)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="ok",
            counters={"files_scanned": 1, "bytes_scanned": payload.size},
            worker_pid=os.getpid(),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, FileNotFoundError) and was_removed(payload.file_path):
            # Removed after its folder was listed, e.g. by a software update:
            # there is nothing to scan, and it is not a failure.  Counted like
            # the encoding skip above, so the progress totals still add up.
            logger.info("skipping %s: file no longer exists (removed after it was listed)", payload.display_path)
            return TaskResult(
                task_id=task.task_id,
                task_type=task.task_type,
                status="ok",
                counters={"files_scanned": 1, "bytes_scanned": payload.size},
                worker_pid=os.getpid(),
            )
        # The coordinator logs the failure; this line only adds the traceback.
        logger.debug("error reading %s", payload.display_path, exc_info=True)
        return TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="error",
            # With the type: str() of a KeyError is only the key, e.g. '7'.
            error_message=f"{type(exc).__name__}: {exc}",
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

    # Count individual match values, not ResultRecord objects, to match CSV row count.
    results_count = sum(len(v) for f in findings for v in f.get("matches", {}).values())
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        findings=findings,
        counters={"files_scanned": 1, "bytes_scanned": payload.size, "results_found": results_count},
        worker_pid=os.getpid(),
    )
