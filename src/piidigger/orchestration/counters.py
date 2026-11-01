"""The counters workers report and the end-of-scan summary reads.

A *_scanned counter means the content was read.  Anything finished without being
read goes to a counter of its own, so the summary can say what was really
scanned while the progress bars, which add them all up, still reach their totals.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

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


def result_counters(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Count individual match values, in total and per data handler, to match the CSV row count."""
    by_handler: Counter[str] = Counter()
    for finding in findings:
        by_handler[f"{RESULTS_BY_HANDLER}{finding['handler']}"] += sum(
            len(values) for values in finding.get("matches", {}).values()
        )
    return {"results_found": by_handler.total(), **by_handler}


def type_tally_text(by_type: Mapping[str, int]) -> str:
    """A by-type tally for a log line, largest first and capped.

    An extension comes from a file name, so it may hold surrogates from a name
    that is not valid UTF-8, which the UTF-8 log file could not write.
    """
    ranked = Counter(by_type).most_common()
    listed = ", ".join(
        f"{kind.encode('utf-8', 'backslashreplace').decode('utf-8')} ×{count:,}"
        for kind, count in ranked[:_MAX_TYPES_LISTED]
    )
    hidden = len(ranked) - _MAX_TYPES_LISTED
    return f"{listed} and {hidden:,} more types" if hidden > 0 else listed
