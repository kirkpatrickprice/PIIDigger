from __future__ import annotations

import logging
from typing import Any

from piidigger.models.results import ResultRecord
from piidigger.orchestration.escaping import escaped_record
from piidigger.orchestration.progress import ProgressDisplay, ResultsFile

_NOT_SAVED = "not saved: the scan was force-quit before the file was closed"


def _value_count(record: ResultRecord) -> int:
    """How many results a record holds, counted as result_counters() counts them."""
    return sum(len(values) for values in record.matches.values())


class GuardedSink:
    """Wraps one opened OutputSink so its errors cannot stop the scan or lose a record.

    Sinks raise OSError and never log.  This wrapper, built in the coordinator
    process, turns the first OSError into one log record and one display event,
    and records it for the end-of-scan summary and exit code.  After that it
    stops writing to the sink: a full disk would otherwise log once per finding.
    close() is still attempted, to release the file handle.

    A ValueError belongs to one record, not to the sink.  No current sink raises
    one: the CSV and text sinks escape text UTF-8 cannot encode, and JSON escapes
    it itself.  The guard is for a future sink, so that a record is never skipped.
    The record is written again with every string escaped, and only a second
    failure stops the sink.  A sink must keep a record only once that record is
    written, as JsonSink does, or a retry adds it twice.  A failure part way
    through a CSV or text record can repeat its first lines, which is better
    than losing them.

    written counts the results that reached the sink, so the summary can check
    each results file against the number of results found.  path names every
    file the sink writes, so a failure in any of them is traced to the right one.

    A plain class rather than a dataclass or model: it holds a live sink and
    logger, and its failure state changes during the run.
    """

    def __init__(self, sink: Any, logger: logging.Logger, progress: ProgressDisplay) -> None:
        self._sink = sink
        self._logger = logger
        self._progress = progress
        self.path = ", ".join(str(path) for path in sink.paths)
        self.label = f"{type(sink).__name__} ({self.path})"
        self.error: str | None = None
        self.written = 0
        # Results may sit in a buffer, or in JsonSink's array, until close().
        self.closed = False

    @property
    def failed(self) -> bool:
        return self.error is not None

    def results_file(self) -> ResultsFile:
        """What the file received, for the summary's check against the results found.

        A file that was never closed is reported as not saved, whatever reached
        the sink: a force-quit skips the close, so buffered results never reach disk.
        """
        if self.failed:
            problem: str | None = f"stopped after an error: {self.error}"
        else:
            problem = None if self.closed else _NOT_SAVED
        return ResultsFile(self.path, self.written, problem)

    def write(self, record: ResultRecord) -> None:
        if self.failed:
            return
        try:
            self._sink.write(record)
        except OSError as exc:
            self._fail("write", exc)
            return
        except ValueError as exc:
            if not self._write_escaped(record, exc):
                return
        self.written += _value_count(record)

    def _write_escaped(self, record: ResultRecord, first: ValueError) -> bool:
        """Write record again with its strings escaped.  True if that worked."""
        escaped = escaped_record(record)
        try:
            self._sink.write(escaped)
        except (OSError, ValueError) as exc:
            self._fail("write", exc)
            return False
        self._logger.warning(
            "%s: wrote the results for %s with their text escaped, after: %s", self.label, escaped.source_path, first
        )
        return True

    def close(self) -> None:
        try:
            self._sink.close()
            self.closed = True
        # ValueError as in write(), so a close that fails is reported as a
        # failure, not mistaken for a force-quit that skipped the close.
        except (OSError, ValueError) as exc:
            if self.failed:
                self._logger.debug("%s: close also failed: %s", self.label, exc)
            else:
                self._fail("close", exc)

    def _fail(self, action: str, exc: Exception) -> None:
        self.error = str(exc)
        self._logger.error("%s: %s failed, no further results written to it: %s", self.label, action, exc)
        self._progress.log_event("ERROR", f"Results file failed: {self.label}: {exc}")
