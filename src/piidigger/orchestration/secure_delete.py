"""Best-effort secure deletion for extracted archive member temp files.

Performs a 2-pass overwrite (zero fill then random fill) before unlinking.

On SSD hardware, physical data remnants may remain after deletion due to
wear-levelling — the OS write may land on a different physical block than
the original data.  This is a hardware limitation that cannot be addressed
in software.  The overwrite-before-delete approach is still applied as a
best effort and is effective on HDD storage.

This limitation is documented in the user guide under Security Considerations.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

# Each overwrite pass writes in chunks of this size, so memory stays fixed
# whatever the file size.  A full-size buffer would cost as much RAM as the
# extracted member, and a MemoryError would skip the unlink below.
_CHUNK_BYTES = 1024 * 1024
_ZERO_CHUNK = bytes(_CHUNK_BYTES)


def secure_delete(path: Path) -> None:
    """Overwrite *path* twice then unlink it.

    Pass 1: zero fill.
    Pass 2: random fill.
    Both passes call os.fsync() to maximise the chance the OS flushes
    writes to the storage device before the file is unlinked.

    Any OSError during the overwrite passes is silenced — the unlink
    attempt still runs so the file is removed even if overwriting fails.
    """
    try:
        size = path.stat().st_size
        if size > 0:
            with path.open("r+b") as f:
                _overwrite_pass(f, size, lambda n: _ZERO_CHUNK[:n])
                _overwrite_pass(f, size, os.urandom)
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _overwrite_pass(f: BinaryIO, size: int, chunk_for: Callable[[int], bytes]) -> None:
    """Overwrite the first *size* bytes of *f* with chunk_for(n) chunks, then fsync."""
    f.seek(0)
    remaining = size
    while remaining > 0:
        n = min(remaining, _CHUNK_BYTES)
        f.write(chunk_for(n))
        remaining -= n
    f.flush()
    os.fsync(f.fileno())


def secure_rmtree(root: Path) -> list[Path]:
    """Securely delete every file under *root*, then remove the directory tree.

    Best effort, and never raises.  Returns every path it had to leave on disk,
    so the caller can tell the user which folder to clean up by hand.  An empty
    list means the whole tree is gone.

    The tree is walked bottom-up.  Each file is overwritten by secure_delete()
    before it is unlinked, and each directory is removed only once it is empty.
    So a folder that cannot be listed (a permission or antivirus error) is
    reported and left alone: its files are never unlinked without being
    overwritten first.  On Windows a file another process holds open without
    share-delete cannot be unlinked; it is reported the same way.

    A symlink is unlinked, never overwritten, so a link can never send the
    overwrite to a file outside *root*.

    Used both per-task by workers and as the run-level backstop, so a temp tree
    left behind by a terminated worker is still overwritten rather than merely
    unlinked.
    """
    if not root.exists():
        return []
    not_removed: list[Path] = []

    def unreadable(exc: OSError) -> None:
        not_removed.append(Path(exc.filename) if exc.filename else root)

    for dirpath, dirnames, filenames in os.walk(root, topdown=False, onerror=unreadable):
        here = Path(dirpath)
        for name in filenames:
            _remove_file(here / name, not_removed)
        for name in dirnames:
            # Walked already (bottom-up), unless it is a symlink, which os.walk
            # does not follow.  rmdir() fails on a folder with leftovers, and
            # those leftovers are reported already.
            sub = here / name
            if sub.is_symlink():
                _remove_file(sub, not_removed)
            else:
                with contextlib.suppress(OSError):
                    sub.rmdir()
    try:
        root.rmdir()
    except OSError:
        if not not_removed:
            not_removed.append(root)
    return not_removed


def _remove_file(path: Path, not_removed: list[Path]) -> None:
    """Remove one file (overwriting it first unless it is a symlink); record it if that fails."""
    try:
        if path.is_symlink():
            path.unlink()
        else:
            secure_delete(path)
    except OSError:
        not_removed.append(path)
