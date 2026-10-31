"""A read-only file that reports each read, so a long read keeps its task alive.

Archive readers (tarfile, py7zr, zipfile) pull compressed bytes through this
wrapper, and so do file handlers that read through FilesystemItem.open_stream()
(pypdf, openpyxl).  A read calls on_progress(), which the worker turns into a
rate-limited TaskProgress "alive" message.  The signal is honest: it fires only
while bytes are actually being read.  A reader stuck inside a decoder reads
nothing, sends nothing, and still trips the task deadline.

With new_bytes_only, only a read of bytes not read before counts.  That is for
the random-access readers behind open_stream(): a parser stuck in a loop that
seeks back and re-reads the same region would otherwise stay alive forever.
Archive readers go front to back, so every read is new and they skip the
bookkeeping.

It lives at the package root because both archivehandlers and orchestration
use it, and archivehandlers must not import orchestration.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from pathlib import Path

# For archive readers.  Large enough that a read call per buffer costs nothing
# next to decompression, small enough that progress arrives several times a
# second even for LZMA.  Random-access readers (pypdf, openpyxl) use Python's
# default instead: every seek outside the buffer refills all of it.
_ARCHIVE_BUFFER_SIZE: int = 1024 * 1024

# The unit new_bytes_only tracks reads in.  A bitmap of one bit per block costs
# about 2 KB per GB of file.
_BLOCK_SIZE: int = 64 * 1024


def _no_progress() -> None:
    """Default on_progress: report nothing."""


def _mark_read(read_blocks: bytearray, start: int, count: int) -> bool:
    """Mark the blocks [start, start + count) covers as read.  True if any was new."""
    first = start // _BLOCK_SIZE
    last = (start + count - 1) // _BLOCK_SIZE
    if last // 8 >= len(read_blocks):
        # The file grew after it was opened.
        read_blocks.extend(bytes(last // 8 + 1 - len(read_blocks)))
    new = False
    for block in range(first, last + 1):
        byte, bit = divmod(block, 8)
        if not read_blocks[byte] & (1 << bit):
            read_blocks[byte] |= 1 << bit
            new = True
    return new


class _ProgressRaw(io.RawIOBase):
    """Unbuffered file reader that calls on_progress after a read that returns data.

    With new_bytes_only, only after a read that touches a block not read before.
    """

    def __init__(self, path: Path, on_progress: Callable[[], None], *, new_bytes_only: bool = False) -> None:
        super().__init__()
        self._raw = io.FileIO(path, "rb")
        self._on_progress = on_progress
        self._name = str(path)
        # One bit per _BLOCK_SIZE block already read, or None when every read counts.
        self._read_blocks: bytearray | None = None
        if new_bytes_only:
            blocks = -(-os.fstat(self._raw.fileno()).st_size // _BLOCK_SIZE)
            self._read_blocks = bytearray(-(-blocks // 8))

    @property
    def name(self) -> str:
        return self._name

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview | bytearray) -> int:  # type: ignore[override]
        read_blocks = self._read_blocks
        start = self._raw.tell() if read_blocks is not None else 0
        count = self._raw.readinto(buffer)
        # Only a read that returned data is progress.  A decoder fed a corrupt
        # archive can spin at end-of-file, reading 0 bytes forever; reporting
        # those reads would keep a stuck task alive past its deadline.
        if count and (read_blocks is None or _mark_read(read_blocks, start, count)):
            self._on_progress()
        return count

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._raw.seek(offset, whence)

    def tell(self) -> int:
        return self._raw.tell()

    def close(self) -> None:
        # IOBase.__del__ calls close() even when __init__ failed (the file could
        # not be opened), so _raw may not exist yet.
        raw = getattr(self, "_raw", None)
        if raw is not None:
            raw.close()
        super().close()


def open_with_progress(
    path: Path,
    on_progress: Callable[[], None] | None = None,
    *,
    buffer_size: int = _ARCHIVE_BUFFER_SIZE,
    new_bytes_only: bool = False,
) -> io.BufferedReader:
    """Open path for binary reading, calling on_progress after every read from disk that returns data.

    With new_bytes_only, only after a read of bytes not read before (see the
    module docstring).  The defaults suit archive readers; FilesystemItem
    passes io.DEFAULT_BUFFER_SIZE and new_bytes_only=True.

    Returns a buffered, seekable file object.  It is an io.IOBase, which matters
    for py7zr: handed a file object instead of a path, py7zr extracts on one
    thread, so members finish strictly in archive order.
    """
    raw = _ProgressRaw(path, on_progress or _no_progress, new_bytes_only=new_bytes_only)
    return io.BufferedReader(raw, buffer_size=buffer_size)
