from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import IO

from piidigger._progress_io import open_with_progress


class FilesystemItem:
    """Wraps a local filesystem path as a ScannableItem.

    MIME type is injected by the caller (detected during directory enumeration
    and passed via ScanFilePayload) rather than detected here, to avoid
    redundant puremagic calls.

    When archive_path and member_path are provided, display_path returns the
    archive::member form so any log messages referencing this item show the
    original archive context rather than the temp file path.

    materialize() returns the path itself — no temp copy needed for on-disk files.
    open_stream() returns a buffered, seekable binary file handle; the caller is
    responsible for closing it.  When on_progress is given, every read from disk
    of bytes not read before calls it, so a handler that reads a large file
    slowly keeps its task alive without knowing about progress at all.
    open_bytes() returns None — signals binary handlers to use the materialize()
    path, which avoids loading large files fully into memory.
    report_progress() passes a file handler's own progress (a finished PDF page)
    to on_progress, for work that neither reads new bytes nor yields content.
    report_unread() records why part of the file was not read, for the worker
    to read back from unread_reason once the handler has finished.  A file can
    have several reasons; unread_reason gives them all.  nothing_read says the
    handler could read none of it, so the worker does not count it as scanned.
    report_no_text() records a document read without error that held no text, for
    the worker to read back from no_text.
    """

    def __init__(
        self,
        path: Path,
        mime: str | None = None,
        *,
        archive_path: Path | None = None,
        member_path: str | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        self._path = path
        self._mime = mime
        self._archive_path = archive_path
        self._member_path = member_path
        self._on_progress = on_progress
        self._unread_reasons: list[str] = []
        self._nothing_read = False
        self._no_text = False

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
        # Python's default buffer, not the archive readers' 1 MiB: pypdf and
        # openpyxl seek around, and every seek outside the buffer refills it.
        # Only reads of new bytes count, so a parser looping over the same
        # region cannot keep its task alive.
        return open_with_progress(  # caller owns the close
            self._path, self._on_progress, buffer_size=io.DEFAULT_BUFFER_SIZE, new_bytes_only=True
        )

    def open_bytes(self) -> bytes | None:
        return None

    def materialize(self) -> Path:
        return self._path

    def report_progress(self) -> None:
        if self._on_progress is not None:
            self._on_progress()

    def report_unread(self, reason: str, *, nothing_read: bool = False) -> None:
        self._unread_reasons.append(reason)
        self._nothing_read = self._nothing_read or nothing_read

    def report_no_text(self) -> None:
        self._no_text = True

    @property
    def unread_reason(self) -> str | None:
        return "; ".join(self._unread_reasons) or None

    @property
    def nothing_read(self) -> bool:
        return self._nothing_read

    @property
    def no_text(self) -> bool:
        return self._no_text
