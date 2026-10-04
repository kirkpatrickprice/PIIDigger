from __future__ import annotations

import logging
from typing import Any

from piidigger.models.results import ResultRecord
from piidigger.orchestration.progress import ProgressDisplay


class GuardedSink:
    """Wraps one opened OutputSink so its I/O errors cannot stop the scan.

    Sinks raise OSError and never log.  This wrapper, built in the coordinator
    process, turns the first failure into one log record and one display event,
    and records it for the end-of-scan summary and exit code.  After that it
    stops writing to the sink: a full disk would otherwise log once per finding.
    close() is still attempted, to release the file handle.

    A plain class rather than a dataclass or model: it holds a live sink and
    logger, and its failure state changes during the run.
    """

    def __init__(self, sink: Any, logger: logging.Logger, progress: ProgressDisplay) -> None:
        self._sink = sink
        self._logger = logger
        self._progress = progress
        self.label = f"{type(sink).__name__} ({sink.path})"
        self.error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    def write(self, record: ResultRecord) -> None:
        if self.failed:
            return
        try:
            self._sink.write(record)
        except OSError as exc:
            self._fail("write", exc)

    def close(self) -> None:
        try:
            self._sink.close()
        except OSError as exc:
            if self.failed:
                self._logger.debug("%s: close also failed: %s", self.label, exc)
            else:
                self._fail("close", exc)

    def _fail(self, action: str, exc: OSError) -> None:
        self.error = str(exc)
        self._logger.error("%s: %s failed, no further results written to it: %s", self.label, action, exc)
        self._progress.log_event("ERROR", f"Results file failed: {self.label}: {exc}")
        self._progress.report_output_failure(self.label, self.error)
