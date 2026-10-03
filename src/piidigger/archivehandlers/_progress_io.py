"""A read-only archive file that reports each read, so long decompression keeps a task alive.

Archive readers (tarfile, py7zr, zipfile) pull compressed bytes through this
wrapper.  Every read calls on_progress(), which the worker turns into a
rate-limited TaskProgress "alive" message.  The signal is honest: it fires only
while bytes are actually being read.  A reader stuck inside a decoder reads
nothing, sends nothing, and still trips the task deadline.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

# Large enough that a read call per buffer costs nothing next to decompression,
# small enough that progress arrives several times a second even for LZMA.
_BUFFER_SIZE: int = 1024 * 1024


def _no_progress() -> None:
    """Default on_progress: report nothing."""


class _ProgressRaw(io.RawIOBase):
    """Unbuffered file reader that calls on_progress after every read."""

    def __init__(self, path: Path, on_progress: Callable[[], None]) -> None:
        super().__init__()
        self._raw = io.FileIO(path, "rb")
        self._on_progress = on_progress
        self._name = str(path)

    @property
    def name(self) -> str:
        return self._name

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview | bytearray) -> int:  # type: ignore[override]
        count = self._raw.readinto(buffer)
        # Only a read that returned data is progress.  A decoder fed a corrupt
        # archive can spin at end-of-file, reading 0 bytes forever; reporting
        # those reads would keep a stuck task alive past its deadline.
        if count:
            self._on_progress()
        return count

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._raw.seek(offset, whence)

    def tell(self) -> int:
        return self._raw.tell()

    def close(self) -> None:
        self._raw.close()
        super().close()


def open_with_progress(path: Path, on_progress: Callable[[], None] | None = None) -> io.BufferedReader:
    """Open path for binary reading, calling on_progress after every read from disk that returns data.

    Returns a buffered, seekable file object.  It is an io.IOBase, which matters
    for py7zr: handed a file object instead of a path, py7zr extracts on one
    thread, so members finish strictly in archive order.
    """
    return io.BufferedReader(_ProgressRaw(path, on_progress or _no_progress), buffer_size=_BUFFER_SIZE)
