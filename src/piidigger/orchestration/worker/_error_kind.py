"""Classify a worker-side exception as an ErrorKind for TaskResult.error_kind."""

from __future__ import annotations

import os
from pathlib import Path

from piidigger.models.tasks import ErrorKind

# Windows raises these lock errors as a PermissionError for a file or drive
# another program holds, such as an open Outlook .pst.  Closing the program,
# not a permission, is the fix, so they stay errors.  ERROR_SHARING_VIOLATION
# (32), ERROR_LOCK_VIOLATION (33), ERROR_DRIVE_LOCKED (108), ERROR_NOT_LOCKED
# (158) and ERROR_LOCK_FAILED (167).
#
# Only a Win32 call such as a folder listing or stat says which.  open() goes
# through the C runtime, which reports a locked file as plain errno 13 with no
# winerror, so a file held open reads as access denied.  Accepted: the user
# guide says so, rather than probing Windows for the reason.
_WINDOWS_LOCKED: frozenset[int] = frozenset({32, 33, 108, 158, 167})


def error_kind_of(exc: BaseException, *, path: str | os.PathLike[str] | None = None) -> ErrorKind | None:
    """The ErrorKind for exc, or None for a failure with no kind of its own.

    Archive handlers wrap the OS error in ArchiveReadError, so the exception's
    direct cause counts too.  With path, only a refusal of path itself is access
    denied.  An archive batch passes its archive, so a refusal of the scan's
    own temp folder (an antivirus blocking an extracted member, say) stays an
    error: no permission on the archive would fix it.
    """
    for candidate in (exc, exc.__cause__):
        if not isinstance(candidate, PermissionError) or getattr(candidate, "winerror", None) in _WINDOWS_LOCKED:
            continue
        if path is None or (candidate.filename is not None and Path(candidate.filename) == Path(path)):
            return "access_denied"
    return None
