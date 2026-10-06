import csv
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

    I/O errors propagate as OSError; the caller decides how to report them.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None
        self._writer = None

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.path,)

    def open(self) -> None:
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        try:
            self._writer = csv.DictWriter(self._file, fieldnames=_FIELDNAMES)
            self._writer.writeheader()
        except BaseException:
            self._file.close()
            self._file = None
            self._writer = None
            raise

    def write(self, record: ResultRecord) -> None:
        if self._writer is None:
            return
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

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
                self._writer = None
