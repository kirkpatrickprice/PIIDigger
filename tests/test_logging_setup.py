"""Unit tests for third-party library log routing."""

from __future__ import annotations

import logging
import logging.handlers
import queue
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from piidigger.getmime import get_mime
from piidigger.orchestration.logging_setup import (
    route_library_logs,
    start_listener,
    stop_library_log_routing,
    stop_listener,
)


@pytest.fixture
def log_queue() -> Iterator[queue.Queue[logging.LogRecord]]:
    q: queue.Queue[logging.LogRecord] = queue.Queue()
    pypdf_level = logging.getLogger("pypdf").level
    yield q
    stop_library_log_routing()
    logging.getLogger("pypdf").setLevel(pypdf_level)


def _drain(q: queue.Queue[logging.LogRecord]) -> list[logging.LogRecord]:
    records = []
    while not q.empty():
        records.append(q.get_nowait())
    return records


@pytest.mark.unit
def test_library_warnings_reach_the_queue(log_queue: queue.Queue[logging.LogRecord]) -> None:
    route_library_logs(log_queue)  # type: ignore[arg-type]  # queue.Queue stands in for mp.Queue

    logging.getLogger("somelib.module").warning("library warning")
    logging.getLogger("somelib.module").info("library chatter")

    messages = [r.getMessage() for r in _drain(log_queue)]
    assert messages == ["library warning"], "WARNING and above only"


@pytest.mark.unit
def test_pypdf_is_held_at_error(log_queue: queue.Queue[logging.LogRecord]) -> None:
    route_library_logs(log_queue)  # type: ignore[arg-type]

    logging.getLogger("pypdf._reader").warning("recoverable defect")
    logging.getLogger("pypdf._reader").error("real problem")

    assert [r.getMessage() for r in _drain(log_queue)] == ["real problem"]


@pytest.mark.unit
def test_routing_is_idempotent_and_removable(log_queue: queue.Queue[logging.LogRecord]) -> None:
    root = logging.getLogger()

    route_library_logs(log_queue)  # type: ignore[arg-type]
    route_library_logs(log_queue)  # type: ignore[arg-type]
    assert sum(isinstance(h, logging.handlers.QueueHandler) for h in root.handlers) == 1

    stop_library_log_routing()
    assert not any(isinstance(h, logging.handlers.QueueHandler) for h in root.handlers)


@pytest.mark.unit
def test_log_file_keeps_each_message_on_one_line(tmp_path: Path) -> None:
    """Multi-line exception text (tarfile lists every method it tried) must not split a record."""
    log_q: queue.Queue[logging.LogRecord] = queue.Queue()
    log_file = tmp_path / "run.log"
    listener = start_listener(log_q, log_file, "INFO")  # type: ignore[arg-type]
    logger = logging.getLogger("tests.single_line")
    logger.propagate = False
    handler = logging.handlers.QueueHandler(log_q)
    logger.addHandler(handler)
    try:
        logger.warning("cannot read archive %s: %s", "a.tar", "not a tar file\n- method gz: ReadError")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("with traceback")
    finally:
        logger.removeHandler(handler)
        stop_listener(listener)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("cannot read archive a.tar: not a tar file - method gz: ReadError")
    assert lines[1].endswith("with traceback")
    assert lines[2] == "Traceback (most recent call last):", "tracebacks keep their own lines"


@pytest.mark.unit
def test_mime_detection_does_not_emit_syntax_warnings(tmp_path: Path) -> None:
    """puremagic ast.parse()s text files; their invalid escapes must not flood the log."""
    # Parses as Python, so the scanner's ast.parse() gets far enough to warn.
    # At least 100 bytes: the scanner skips smaller files without a .py name.
    js = tmp_path / "bundle"
    js.write_text('path = "\\/usr\\/lib"\npattern = "\\d+"\n' * 10)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        get_mime(str(js))

    assert not [w for w in caught if issubclass(w.category, SyntaxWarning)]
