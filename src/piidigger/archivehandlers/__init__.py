from __future__ import annotations

from typing import TYPE_CHECKING

from piidigger.archivehandlers import _7z, _tar, _zip

if TYPE_CHECKING:
    from piidigger.protocols import ArchiveHandler

_MODULES = (_zip, _7z, _tar)

# HANDLER_REGISTRY: archive_type string → handler instance.
# Keys are bare ARCHIVE_TYPE values ("zip", "7z", "tar") — used by worker
# handlers that receive archive_type from the task payload.
HANDLER_REGISTRY: dict[str, ArchiveHandler] = {}
for _mod in _MODULES:
    HANDLER_REGISTRY[_mod.ARCHIVE_TYPE] = _mod.handler

# _EXT_REGISTRY: (bare_ext_without_dot, archive_type) pairs sorted longest-first.
# Longest-first ordering is required for correct compound-suffix matching via
# endswith() — "tar.gz" must be tried before "gz" so it wins for "data.tar.gz".
_EXT_REGISTRY: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            (ext.lstrip(".").lower(), _mod.ARCHIVE_TYPE)
            for _mod in _MODULES
            for ext in _mod.HANDLES["ext"]
        ),
        key=lambda pair: -len(pair[0]),
    )
)


def get_handler(archive_type: str) -> ArchiveHandler | None:
    return HANDLER_REGISTRY.get(archive_type)


def detect_archive_type(filename: str) -> str | None:
    """Map a filename to a registered archive_type, or None if unrecognized.

    Handles single-suffix formats (data.zip -> "zip") and tar's compound
    and aliased suffixes (data.tar.gz -> "tar", data.tgz -> "tar").
    """
    name = filename.lower()
    for bare_ext, archive_type in _EXT_REGISTRY:
        if name.endswith(f".{bare_ext}"):
            return archive_type
    return None
