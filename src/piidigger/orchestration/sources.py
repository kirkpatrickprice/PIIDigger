from __future__ import annotations

import zipfile
from pathlib import Path
from typing import IO


class FilesystemItem:
    """Wraps a local filesystem path as a ScannableItem.

    MIME type is injected by the caller (detected during directory enumeration
    and passed via ScanFilePayload) rather than detected here, to avoid
    redundant puremagic calls.

    materialize() returns the path itself — no temp copy needed for on-disk files.
    open_stream() returns an unbuffered binary file handle; the caller is
    responsible for closing it.
    open_bytes() returns None — signals binary handlers to use the materialize()
    + read_only streaming path, which avoids loading large XLSX files fully into
    memory.
    """

    def __init__(self, path: Path, mime: str | None = None) -> None:
        self._path = path
        self._mime = mime

    @property
    def display_path(self) -> str:
        return str(self._path)

    @property
    def ext(self) -> str:
        return self._path.suffix

    @property
    def mime(self) -> str | None:
        return self._mime

    @property
    def size(self) -> int:
        return self._path.stat().st_size

    @property
    def depth(self) -> int:
        return 0

    def open_stream(self) -> IO[bytes]:
        return open(self._path, "rb")  # noqa: WPS515 — caller owns the close

    def open_bytes(self) -> bytes | None:
        return None

    def materialize(self) -> Path:
        return self._path


class ArchiveMemberItem:
    """Wraps a single member inside a ZIP archive as a ScannableItem.

    Holds only pickle-safe fields (Path + str) so the item can be constructed
    inside a worker process from a ScanArchiveMemberPayload without any
    live ZipFile handle crossing the process boundary.

    Each call to open_stream() / open_bytes() / materialize() opens a fresh
    ZipFile handle and closes it before returning, so callers do not need to
    manage the archive handle lifecycle.

    open_bytes() returns the member content fully buffered in memory.  This is
    the preferred path for all three binary handlers (docx2python, openpyxl,
    xlrd all accept BytesIO / bytes directly).  Members are bounded by
    ArchiveConfig.max_member_uncompressed_size_mb (default 50 MB) so in-memory
    buffering is safe.

    materialize() extracts to a flat file inside task_temp and is used only
    when a handler genuinely requires a filesystem path.  The worker loop's
    _cleanup_temp_workspace() securely deletes the task_temp directory after
    each task completes.
    """

    def __init__(
        self,
        archive_path: Path,
        member_path: str,
        uncompressed_size: int,
        mime: str | None,
        depth: int,
        task_temp: Path,
    ) -> None:
        self._archive_path = archive_path
        self._member_path = member_path
        self._uncompressed_size = uncompressed_size
        self._mime = mime
        self._depth = depth
        self._task_temp = task_temp

    @property
    def display_path(self) -> str:
        return f"{self._archive_path}::{self._member_path}"

    @property
    def ext(self) -> str:
        return Path(self._member_path).suffix

    @property
    def mime(self) -> str | None:
        return self._mime

    @property
    def size(self) -> int:
        return self._uncompressed_size

    @property
    def depth(self) -> int:
        return self._depth

    def open_stream(self) -> IO[bytes]:
        """Return a binary stream for the member.

        Opens a fresh ZipFile; the returned stream holds a reference to it
        and will close both when the stream is closed.
        """
        zf = zipfile.ZipFile(self._archive_path, "r")
        return zf.open(self._member_path)  # noqa: WPS515 — caller owns close; closes zf too

    def open_bytes(self) -> bytes:
        """Return the full member content as bytes (in-memory, no temp file)."""
        with zipfile.ZipFile(self._archive_path, "r") as zf:
            return zf.read(self._member_path)

    def materialize(self) -> Path:
        """Extract the member to a flat file inside task_temp and return its path.

        Uses open_bytes() so the ZipFile is opened and closed within this call.
        Extraction uses the member's base filename only (no subdirectory
        structure) to avoid nested path complications.

        The caller (worker loop via _cleanup_temp_workspace) owns deletion.
        secure_delete() is called on the extracted file during cleanup.
        """
        self._task_temp.mkdir(parents=True, exist_ok=True)
        dest = self._task_temp / Path(self._member_path).name
        dest.write_bytes(self.open_bytes())
        return dest
