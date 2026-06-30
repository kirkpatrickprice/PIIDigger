from __future__ import annotations

from typing import TYPE_CHECKING

from piidigger.archivehandlers import _7z, _zip

if TYPE_CHECKING:
    from piidigger.protocols import ArchiveHandler

# Keys are the bare extension without the leading dot, lowercase ("zip", "7z").
HANDLER_REGISTRY: dict[str, ArchiveHandler] = {}

for _mod in (_zip, _7z):
    HANDLER_REGISTRY[_mod.ARCHIVE_TYPE] = _mod.handler


def get_handler(archive_type: str) -> ArchiveHandler | None:
    return HANDLER_REGISTRY.get(archive_type)
