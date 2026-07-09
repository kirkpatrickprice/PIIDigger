import json
import logging
from pathlib import Path

from piidigger.models.results import ResultRecord


class JsonSink:
    """OutputSink that writes findings as JSON.

    Produces two output files:
    - <stem>.jsonl  — one JSON object per line, appended during the scan (streaming)
    - <path>        — full JSON array written at close() (or Ctrl+C via finally block)

    Only a hard kill (SIGKILL) loses the .json array; clean exit and Ctrl+C
    both flush it because the coordinator calls close() from a finally block.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._jsonl_path = path.with_suffix(".jsonl")
        self._file = None
        self._records: list[dict] = []
        self._logger = logging.getLogger(__name__)

    def open(self) -> None:
        try:
            self._file = open(self._jsonl_path, "w", encoding="utf-8")
        except OSError as e:
            self._logger.error("JsonSink: cannot open %s: %s", self._jsonl_path, e)

    def write(self, record: ResultRecord) -> None:
        data = record.model_dump()
        self._records.append(data)
        if self._file is not None:
            try:
                self._file.write(json.dumps(data) + "\n")
            except OSError as e:
                self._logger.error("JsonSink: write error: %s", e)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except OSError as e:
                self._logger.error("JsonSink: jsonl close error: %s", e)
            finally:
                self._file = None
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, indent=2)
        except OSError as e:
            self._logger.error("JsonSink: cannot write %s: %s", self._path, e)
