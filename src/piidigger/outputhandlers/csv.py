import csv
import logging
from pathlib import Path

from piidigger.models.results import ResultRecord

_FIELDNAMES = [
    "source_path",
    "source_member_path",
    "source_depth",
    "source_container_type",
    "handler",
    "match_type",
    "value",
]


class CsvSink:
    """OutputSink that writes findings as CSV rows.

    Each (match_type, value) pair in a ResultRecord becomes one row.
    Lineage fields are written even when null (on-disk files).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file = None
        self._writer = None
        self._logger = logging.getLogger(__name__)

    def open(self) -> None:
        try:
            self._file = open(self._path, "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._file, fieldnames=_FIELDNAMES)
            self._writer.writeheader()
        except OSError as e:
            self._logger.error("CsvSink: cannot open %s: %s", self._path, e)

    def write(self, record: ResultRecord) -> None:
        if self._writer is None:
            return
        try:
            for match_type, values in record.matches.items():
                for value in values:
                    self._writer.writerow(
                        {
                            "source_path": record.source_path,
                            "source_member_path": record.source_member_path,
                            "source_depth": record.source_depth,
                            "source_container_type": record.source_container_type,
                            "handler": record.handler,
                            "match_type": match_type,
                            "value": value,
                        }
                    )
        except OSError as e:
            self._logger.error("CsvSink: write error: %s", e)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except OSError as e:
                self._logger.error("CsvSink: close error: %s", e)
            finally:
                self._file = None
                self._writer = None
