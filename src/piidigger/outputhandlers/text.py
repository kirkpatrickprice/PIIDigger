from pathlib import Path

from piidigger.models.results import ResultRecord


class TextSink:
    """OutputSink that writes findings as pipe-separated text lines.

    Format: source_path | handler | match_type | value
    Archive lineage fields (source_member_path, source_depth, source_container_type)
    are appended as key=value tokens when non-null / non-zero.

    I/O errors propagate as OSError; the caller decides how to report them.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.path,)

    def open(self) -> None:
        self._file = open(self.path, "w", encoding="utf-8")

    def write(self, record: ResultRecord) -> None:
        if self._file is None:
            return
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

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
