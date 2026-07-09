from __future__ import annotations


class ArchiveReadError(Exception):
    """Raised by ArchiveHandler implementations when an archive cannot be opened or listed.

    Each format module catches its own library-specific exceptions
    (BadZipFile, py7zr exceptions, tarfile.TarError, …) and re-raises as
    ArchiveReadError so callers stay format-agnostic.
    """
