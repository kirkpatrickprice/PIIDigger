"""Run a file handler and the data handlers over one file, keeping its task alive.

Shared by handle_scan_file (files on disk) and _scan_member (extracted archive
members), so the rules for what counts as progress live in one place.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from piidigger.models.config import Config
from piidigger.orchestration.sources import FilesystemItem
from piidigger.protocols import DataHandler, FileHandler


def scan_content(
    file_handler: FileHandler,
    item: FilesystemItem,
    config: Config,
    enabled_handlers: Sequence[DataHandler],
    on_progress: Callable[[], None],
) -> dict[str, dict[str, set[str]]]:
    """Return every data handler's matches across all the content the file handler yields.

    The result maps a data handler's name to its matches by type, merged over
    every piece of content, so the caller can make one ResultRecord per handler.

    on_progress is called for each non-empty piece of content.  An empty one is
    not progress, by the same rule as a read that returns no data: a handler
    stuck yielding "" must still trip the deadline.  Reads from disk report
    progress separately, through the item's open_stream().

    Raises whatever the file handler raises; the caller decides what a failure
    means.
    """
    per_handler: dict[str, dict[str, set[str]]] = {}
    for content in file_handler.read(item, config):
        if not content:
            continue
        on_progress()
        for dh in enabled_handlers:
            for match_type, values in dh.find_matches(content).items():
                if values:
                    per_handler.setdefault(dh.name, {}).setdefault(match_type, set()).update(values)
    return per_handler
