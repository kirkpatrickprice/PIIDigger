import json
from pathlib import Path

from piidigger.models.results import ResultRecord


def _naming(error: OSError, path: Path) -> OSError:
    """error, naming path if it names no file.

    A write error such as a full disk names no file.  This sink writes two, so
    its error must say which one failed.  Only an error with an errno takes the
    name: setting it on one without would turn its message into "[Errno None] None".
    """
    if error.filename is None and error.errno is not None:
        error.filename = str(path)
    return error


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

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.path, self._jsonl_path)

    def open(self) -> None:
        self._file = open(self._jsonl_path, "w", encoding="utf-8")

    def write(self, record: ResultRecord) -> None:
        """Stream the record to the .jsonl file, then keep it for the .json array.

        A record the stream failed to take is not kept, so the array never holds
        more than the caller counted as written, and a retried record appears once.
        """
        data = record.model_dump()
        if self._file is not None:
            try:
                self._file.write(json.dumps(data) + "\n")
            except OSError as e:
                raise _naming(e, self._jsonl_path) from None
        self._records.append(data)

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
                error = _naming(e, self._jsonl_path)
            finally:
                self._file = None
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, indent=2)
        except OSError as e:
            error = error or _naming(e, self.path)
        if error is not None:
            raise error
