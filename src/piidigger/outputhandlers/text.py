import logging
from pathlib import Path

from piidigger.models.results import ResultRecord


class TextSink:
    """OutputSink that writes findings as pipe-separated text lines.

    Format: source_path | handler | match_type | value
    Archive lineage fields (source_member_path, source_depth, source_container_type)
    are appended as key=value tokens when non-null / non-zero.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file = None
        self._logger = logging.getLogger(__name__)

    def open(self) -> None:
        try:
            self._file = open(self._path, "w", encoding="utf-8")
        except OSError as e:
            self._logger.error("TextSink: cannot open %s: %s", self._path, e)

    def write(self, record: ResultRecord) -> None:
        if self._file is None:
            return
        try:
            for match_type, values in record.matches.items():
                for value in values:
                    parts = [record.source_path, record.handler, match_type, value]
                    if record.source_member_path is not None:
                        parts.append(f"member={record.source_member_path}")
                    if record.source_depth > 0:
                        parts.append(f"depth={record.source_depth}")
                    if record.source_container_type is not None:
                        parts.append(f"container={record.source_container_type}")
                    self._file.write(" | ".join(parts) + "\n")
        except OSError as e:
            self._logger.error("TextSink: write error: %s", e)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except OSError as e:
                self._logger.error("TextSink: close error: %s", e)
            finally:
                self._file = None
