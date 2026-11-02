"""The counters workers report and the end-of-scan summary reads.

A *_scanned counter means the content was read.  Anything finished without being
read goes to a counter of its own, so the summary can say what was really
scanned while the progress bars, which add them all up, still reach their totals.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from piidigger.orchestration.escaping import utf8_escaped

# What each progress bar counts as finished.
DIRS_DONE: tuple[str, ...] = ("dirs_scanned", "dirs_not_scanned")
# An archive counts as a file found, but only its members are scanned as files.
FILES_DONE: tuple[str, ...] = ("files_scanned", "files_unreadable", "files_not_scanned", "archives_opened")
BYTES_DONE: tuple[str, ...] = ("bytes_scanned", "bytes_not_scanned")

# Prefixes of counters with one key per kind, so a new data handler or file type
# needs no change here.
RESULTS_BY_HANDLER: str = "results_by_handler:"
UNSUPPORTED_BY_TYPE: str = "files_unsupported_by_type:"

# How many types a by-type log line lists before "and N more types".
_MAX_TYPES_LISTED: int = 15

# Common documents PIIDigger has no reader for.  The end-of-scan tally always lists
# these, so a long tail of binary types cannot hide them.
_UNREAD_DOCUMENT_TYPES: frozenset[str] = frozenset({".doc", ".msg", ".ppt", ".pptx", ".rtf"})


def result_counters(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Count individual match values, in total and per data handler, to match the CSV row count."""
    by_handler: Counter[str] = Counter()
    for finding in findings:
        by_handler[f"{RESULTS_BY_HANDLER}{finding['handler']}"] += sum(
            len(values) for values in finding.get("matches", {}).values()
        )
    return {"results_found": by_handler.total(), **by_handler}


def _tally_text(ranked: list[tuple[str, int]], limit: int) -> str:
    """The first limit (kind, count) pairs as text, then how many were left out.

    An extension comes from a file name, so it may hold surrogates from a name
    that is not valid UTF-8, which the UTF-8 log file could not write.
    """
    listed = ", ".join(f"{utf8_escaped(kind)} ×{count:,}" for kind, count in ranked[:limit])
    hidden = len(ranked) - limit
    return f"{listed} and {hidden:,} more types" if hidden > 0 else listed


def type_tally_text(by_type: Mapping[str, int]) -> str:
    """A by-type tally for a log line, largest first and capped."""
    return _tally_text(Counter(by_type).most_common(), _MAX_TYPES_LISTED)


def unsupported_tally_text(by_type: Mapping[str, int]) -> str:
    """The end-of-scan tally: every common document type, then the others up to the usual cap."""
    ranked = Counter(by_type).most_common()
    documents = [entry for entry in ranked if entry[0] in _UNREAD_DOCUMENT_TYPES]
    others = [entry for entry in ranked if entry[0] not in _UNREAD_DOCUMENT_TYPES]
    return _tally_text(documents + others, len(documents) + _MAX_TYPES_LISTED)
