"""Unit tests for error_kind_of: which worker exceptions count as access denied."""

from __future__ import annotations

import pytest

from piidigger.exceptions import ArchiveReadError
from piidigger.orchestration.worker._error_kind import error_kind_of


def _windows_error(winerror: int) -> PermissionError:
    """A PermissionError as Windows raises it.  Only Windows sets winerror, so it is set by hand."""
    exc = PermissionError(13, "The process cannot access the file")
    exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


def _wrapped(cause: BaseException) -> ArchiveReadError:
    try:
        raise ArchiveReadError(str(cause)) from cause
    except ArchiveReadError as exc:
        return exc


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (PermissionError(13, "Permission denied"), "access_denied"),
        (_windows_error(5), "access_denied"),  # ERROR_ACCESS_DENIED
        (_wrapped(PermissionError(13, "Permission denied")), "access_denied"),
        # EPERM: on macOS, privacy protection, which Full Disk Access lifts.
        (PermissionError(1, "Operation not permitted"), "not_permitted"),
        (_wrapped(PermissionError(1, "Operation not permitted")), "not_permitted"),
    ],
)
def test_refused_access_is_access_denied(exc: BaseException, kind: str) -> None:
    assert error_kind_of(exc) == kind


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        _windows_error(32),  # ERROR_SHARING_VIOLATION: another program has the file open
        _windows_error(33),  # ERROR_LOCK_VIOLATION
        _windows_error(108),  # ERROR_DRIVE_LOCKED
        _windows_error(158),  # ERROR_NOT_LOCKED
        _windows_error(167),  # ERROR_LOCK_FAILED
        _wrapped(_windows_error(32)),
        OSError(5, "I/O error"),
        _wrapped(OSError(5, "I/O error")),
        ValueError("bad"),
    ],
)
def test_other_failures_have_no_kind(exc: BaseException) -> None:
    """A file locked by another program needs that program closed, not a permission changed."""
    assert error_kind_of(exc) is None


@pytest.mark.unit
def test_with_a_path_only_a_refusal_of_that_path_is_access_denied() -> None:
    """An archive batch whose temp folder is refused is not the archive's permission problem."""
    archive = "/data/a.zip"

    assert error_kind_of(_wrapped(PermissionError(13, "Permission denied", archive)), path=archive) == "access_denied"
    assert (
        error_kind_of(_wrapped(PermissionError(13, "Permission denied", "/tmp/piidigger_x/1.txt")), path=archive)
        is None
    )
    assert error_kind_of(_wrapped(PermissionError(13, "Permission denied")), path=archive) is None, "names no file"
