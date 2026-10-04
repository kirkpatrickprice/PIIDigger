import json
from pathlib import Path

from piidigger.models.results import ResultRecord


class JsonSink:
    """OutputSink that writes findings as JSON.

    Produces two output files:
    - <stem>.jsonl  — one JSON object per line, appended during the scan (streaming)
    - <path>        — full JSON array written at close() (or Ctrl+C via finally block)

    Only a hard kill (SIGKILL) loses the .json array; clean exit and Ctrl+C
    both flush it because the coordinator calls close() from a finally block.

    I/O errors propagate as OSError; the caller decides how to report them.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._jsonl_path = path.with_suffix(".jsonl")
        self._file = None
        self._records: list[dict] = []

    def open(self) -> None:
        self._file = open(self._jsonl_path, "w", encoding="utf-8")

    def write(self, record: ResultRecord) -> None:
        data = record.model_dump()
        self._records.append(data)
        if self._file is not None:
            self._file.write(json.dumps(data) + "\n")

    def close(self) -> None:
        """Close the .jsonl stream, then write the .json array.

        The array is written even if closing the stream fails, so one bad file
        does not cost the other.  The first error is raised afterwards.
        """
        error: OSError | None = None
        if self._file is not None:
            try:
                self._file.close()
            except OSError as e:
                error = e
            finally:
                self._file = None
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, indent=2)
        except OSError as e:
            error = error or e
        if error is not None:
            raise error
