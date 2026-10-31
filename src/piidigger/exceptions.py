from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from piidigger.models.archive import MemberInfo


class ArchiveReadError(Exception):
    """Raised by ArchiveHandler implementations when an archive cannot be opened or listed.

    Each format module catches its own library-specific exceptions
    (BadZipFile, py7zr exceptions, tarfile.TarError, …) and re-raises as
    ArchiveReadError so callers stay format-agnostic.
    """


class ArchivePartlyReadError(ArchiveReadError):
    """Raised by list_members() when an archive fails after some members were listed.

    A stream format such as a compressed tar can only be listed by reading it
    from the start, so damage part way through still leaves the members before
    it readable.  They are in members, for the caller to scan.
    """

    def __init__(self, message: str, members: list[MemberInfo]) -> None:
        super().__init__(message)
        self.members = members


class UndetectableEncodingError(Exception):
    """Raised by the plaintext FileHandler when a non-empty file's encoding cannot be detected.

    Handlers cannot log, so the file would otherwise be a silent skip.  The
    scan handlers catch it, log the file at INFO and count it as scanned:
    it is usually binary content with a text extension, not a read failure.
    """
