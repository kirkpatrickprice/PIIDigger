from __future__ import annotations


class ArchiveReadError(Exception):
    """Raised by ArchiveHandler implementations when an archive cannot be opened or listed.

    Each format module catches its own library-specific exceptions
    (BadZipFile, py7zr exceptions, tarfile.TarError, …) and re-raises as
    ArchiveReadError so callers stay format-agnostic.
    """


class UndetectableEncodingError(Exception):
    """Raised by the plaintext FileHandler when a non-empty file's encoding cannot be detected.

    Handlers cannot log, so the file would otherwise be a silent skip.  The
    scan handlers catch it, log the file at INFO and count it as scanned:
    it is usually binary content with a text extension, not a read failure.
    """
