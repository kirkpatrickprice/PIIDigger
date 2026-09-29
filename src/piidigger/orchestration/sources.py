from __future__ import annotations

from pathlib import Path
from typing import IO


class FilesystemItem:
    """Wraps a local filesystem path as a ScannableItem.

    MIME type is injected by the caller (detected during directory enumeration
    and passed via ScanFilePayload) rather than detected here, to avoid
    redundant puremagic calls.

    When archive_path and member_path are provided, display_path returns the
    archive::member form so any log messages referencing this item show the
    original archive context rather than the temp file path.

    materialize() returns the path itself — no temp copy needed for on-disk files.
    open_stream() returns an unbuffered binary file handle; the caller is
    responsible for closing it.
    open_bytes() returns None — signals binary handlers to use the materialize()
    path, which avoids loading large files fully into memory.
    """

    def __init__(
        self,
        path: Path,
        mime: str | None = None,
        *,
        archive_path: Path | None = None,
        member_path: str | None = None,
    ) -> None:
        self._path = path
        self._mime = mime
        self._archive_path = archive_path
        self._member_path = member_path

    @property
    def display_path(self) -> str:
        if self._archive_path is not None and self._member_path is not None:
            return f"{self._archive_path}::{self._member_path}"
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
