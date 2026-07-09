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
