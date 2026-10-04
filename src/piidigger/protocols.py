from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

from piidigger.models.archive import MemberInfo
from piidigger.models.config import Config
from piidigger.models.results import ResultRecord


@runtime_checkable
class DataHandler(Protocol):
    name: str

    def find_matches(self, text: str) -> dict[str, set[str]]: ...


@runtime_checkable
class ScannableItem(Protocol):
    @property
    def display_path(self) -> str: ...
    @property
    def ext(self) -> str: ...
    @property
    def mime(self) -> str | None: ...
    @property
    def size(self) -> int: ...
    @property
    def depth(self) -> int: ...

    def open_stream(self) -> IO[bytes]: ...
    def open_bytes(self) -> bytes | None: ...
    def materialize(self) -> Path: ...


@runtime_checkable
class FileHandler(Protocol):
    def read(self, source: ScannableItem, config: Config) -> Iterator[str]: ...


@runtime_checkable
class OutputSink(Protocol):
    """Writes findings to one results file.

    Sinks never log.  open(), write() and close() let OSError propagate.
    run_scan treats a failed open() as fatal.  The coordinator wraps each sink
    in a GuardedSink, which logs a write or close failure once and stops
    writing to that sink.  path names the file in those messages.
    """

    path: Path

    def open(self) -> None: ...
    def write(self, record: ResultRecord) -> None: ...
    def close(self) -> None: ...


class ArchiveHandler(Protocol):
    """Implemented by each format module in piidigger/archivehandlers/.

    list_members() inspects the archive without extracting any content to disk.
    extract_members() extracts a run of members one at a time, handing each to
    a callback before it touches the next.  Opening the archive once per run,
    rather than once per member, is what keeps compressed tar and solid 7z
    linear: reaching a member in those formats means decompressing everything
    stored before it.

    Callbacks are plain callables, so handlers never see a queue or a logger.
    """

    def list_members(self, archive_path: Path, on_progress: Callable[[], None] | None = None) -> list[MemberInfo]:
        """Return all entries (dirs and files) from the archive, in the order they are stored.

        on_progress is called as the archive is read, so a long listing (a
        compressed tar is decompressed end to end) keeps its task alive.

        Raises ArchiveReadError on any open or parse failure.
        No content is extracted to disk during this call.
        """
        ...

    def extract_members(
        self,
        archive_path: Path,
        member_paths: Sequence[str],
        dest_dir: Path,
        *,
        on_extracted: Callable[[str, Path], None],
        on_started: Callable[[str], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        """Extract the requested members to dest_dir in archive order, one at a time.

        For each member: on_started(member) just before it is extracted, then
        on_extracted(member, path) once it is on disk.  The handler does not
        move on until on_extracted returns, so the caller can scan and delete
        the file and only one member is ever on disk.  The path may be nested
        under dest_dir.  Creates dest_dir if it does not exist.

        A member that cannot be extracted on its own (say, a filter rejects it)
        is passed to on_failed(member, reason) and the run continues.  A
        requested member never reported to either callback was not found.

        on_progress is called as compressed bytes are read, including while
        skipping the part of the archive before the first requested member.

        Raises ArchiveReadError when the archive itself cannot be read (cannot
        open, corrupt stream, CRC failure).  Members already handed to
        on_extracted stay reported; the rest are not.
        """
        ...
