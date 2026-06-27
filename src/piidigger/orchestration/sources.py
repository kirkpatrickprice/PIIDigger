from __future__ import annotations

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

    def materialize(self) -> Path:
        return self._path
