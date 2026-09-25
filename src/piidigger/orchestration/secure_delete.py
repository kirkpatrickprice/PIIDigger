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

import os
import shutil
from pathlib import Path


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
                f.write(b"\x00" * size)
                f.flush()
                os.fsync(f.fileno())
                f.seek(0)
                f.write(os.urandom(size))
                f.flush()
                os.fsync(f.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def secure_rmtree(root: Path) -> None:
    """Securely delete every file under *root*, then remove the directory tree.

    secure_delete() is called only on files — unlink() raises IsADirectoryError
    on directories, which missing_ok=True does not suppress.  shutil.rmtree()
    then removes the now-empty tree.  No-ops when root does not exist.

    Used both per-task by workers and as the run-level backstop, so a temp tree
    left behind by a terminated worker is still overwritten rather than merely
    unlinked.
    """
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file():
            secure_delete(path)
    shutil.rmtree(root, ignore_errors=True)
