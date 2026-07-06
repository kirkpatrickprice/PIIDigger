from __future__ import annotations

from collections.abc import Iterator
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
    def open(self) -> None: ...
    def write(self, record: ResultRecord) -> None: ...
    def close(self) -> None: ...


class ArchiveHandler(Protocol):
    """Implemented by each format module in piidigger/archivehandlers/.

    list_members() inspects the archive without extracting any content to disk.
    extract_member() extracts one member to a caller-provided directory and
    returns the path to the extracted file.  The caller owns the file's
    lifecycle; cleanup is handled by _cleanup_temp_workspace() in the worker loop.
    """

    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        """Return all entries (dirs and files) from the archive.

        Raises ArchiveReadError on any open or parse failure.
        No content is extracted to disk during this call.
        """
        ...

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        """Extract one member to dest_dir and return the file path.

        Writes to dest_dir / Path(member_path).name (flat — no subdirectory
        nesting).  Creates dest_dir if it does not exist.
        Raises ArchiveReadError on failure.
        """
        ...
