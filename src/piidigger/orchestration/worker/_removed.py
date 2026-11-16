"""Was a path removed after it was listed?  Used to tell a vanished file or folder from an unreadable one."""

from __future__ import annotations

import os
from pathlib import Path


def was_removed(path: Path) -> bool:
    """True only when the nearest ancestor that can still be listed no longer holds path.

    A FileNotFoundError, or path.exists() returning False, does not prove the
    path is gone.  On Windows a path past MAX_PATH, or one through a broken
    component, fails the same way while the file is still there.  So this walks
    up to the nearest ancestor that can be listed and checks the listing.  That
    also covers a whole tree removed at once, as a software update does.

    Any answer short of "the listing no longer has it" is False, so an
    unreadable path is still reported as a failure.  Only called on that error
    path, so the extra listing costs nothing in a normal scan.
    """
    child = path
    for parent in path.parents:
        try:
            names = os.listdir(parent)
        except FileNotFoundError:
            child = parent
            continue
        except OSError:
            return False
        return child.name not in names
    return False
