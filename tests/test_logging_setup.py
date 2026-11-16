"""Unit tests for third-party library log routing."""

from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from piidigger.getmime import get_mime
from piidigger.orchestration.logging_setup import (
    LogQueueHandler,
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
    handler = LogQueueHandler(log_q)
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
    assert lines[2] == "    Traceback (most recent call last):", "tracebacks keep their own lines, indented"


@pytest.mark.unit
def test_log_file_keeps_a_record_naming_a_non_utf8_file(tmp_path: Path) -> None:
    """A POSIX file name that is not valid UTF-8 arrives as surrogate escapes.

    Strict UTF-8 would make logging drop the record, the only report of that file.
    """
    log_q: queue.Queue[logging.LogRecord] = queue.Queue()
    log_file = tmp_path / "run.log"
    listener = start_listener(log_q, log_file, "INFO")  # type: ignore[arg-type]
    logger = logging.getLogger("tests.surrogate_path")
    logger.propagate = False
    handler = LogQueueHandler(log_q)
    logger.addHandler(handler)
    try:
        # %s, not %r: repr() would escape the surrogate before the handler saw it.
        logger.warning("skipping %s: text encoding could not be detected", "/data/caf\udce9.txt")
        logger.error("next record")
    finally:
        logger.removeHandler(handler)
        stop_listener(listener)

    lines = log_file.read_bytes().decode("utf-8").splitlines()
    # The byte the escape stands for on POSIX; the UTF-16 unit itself on Windows.
    escaped = "caf\\xe9.txt" if sys.getfilesystemencodeerrors() == "surrogateescape" else "caf\\udce9.txt"
    assert lines[0].endswith(f"skipping /data/{escaped}: text encoding could not be detected")
    assert lines[1].endswith("next record")


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


@pytest.mark.unit
def test_log_file_escapes_control_characters_in_a_path(tmp_path: Path) -> None:
    """Paths are logged with %s, so a carriage return or escape sequence in a file name reaches the formatter raw."""
    log_q: queue.Queue[logging.LogRecord] = queue.Queue()
    log_file = tmp_path / "run.log"
    listener = start_listener(log_q, log_file, "INFO")  # type: ignore[arg-type]
    logger = logging.getLogger("tests.control_path")
    logger.propagate = False
    handler = LogQueueHandler(log_q)
    logger.addHandler(handler)
    try:
        logger.warning("[scan_file] path=%s: boom", "/data/a\rfake line\x1b[2Kb\tc\nd\x9be f‮g.txt")
    finally:
        logger.removeHandler(handler)
        stop_listener(listener)

    [line] = log_file.read_text(encoding="utf-8").splitlines()
    # \u009b, not \x9b: in the log, \x9b means the non-UTF-8 byte 0x9B.  U+2028
    # would split the line for splitlines(); U+202E reverses it on screen.
    assert line.endswith("[scan_file] path=/data/a\\x0dfake line\\x1b[2Kb\tc d\\u009be\\u2028f\\u202eg.txt: boom")


def _log_lines(tmp_path: Path, *records: tuple[str, tuple[object, ...]]) -> list[str]:
    """Log each (format, args) at WARNING through the real listener, and return the file's lines."""
    log_q: queue.Queue[logging.LogRecord] = queue.Queue()
    log_file = tmp_path / "run.log"
    listener = start_listener(log_q, log_file, "INFO")  # type: ignore[arg-type]
    logger = logging.getLogger("tests.log_lines")
    logger.propagate = False
    handler = LogQueueHandler(log_q)
    logger.addHandler(handler)
    try:
        for fmt, args in records:
            logger.warning(fmt, *args)
    finally:
        logger.removeHandler(handler)
        stop_listener(listener)
    return log_file.read_text(encoding="utf-8").splitlines()


@pytest.mark.unit
def test_traceback_header_inside_a_path_is_part_of_the_message(tmp_path: Path) -> None:
    """Split there, the rest of the name would reach the log unescaped, on lines of its own."""
    name = "/tmp/Traceback (most recent call last):\x1b[2J\nevil.pdf"

    [line] = _log_lines(tmp_path, ("skipping %s: %s", (name, "bad")))

    assert line.endswith("skipping /tmp/Traceback (most recent call last):\\x1b[2J evil.pdf: bad")


@pytest.mark.unit
def test_a_traceback_is_escaped_too(tmp_path: Path) -> None:
    log_q: queue.Queue[logging.LogRecord] = queue.Queue()
    log_file = tmp_path / "run.log"
    listener = start_listener(log_q, log_file, "INFO")  # type: ignore[arg-type]
    logger = logging.getLogger("tests.escaped_traceback")
    logger.propagate = False
    handler = LogQueueHandler(log_q)
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("bad\x1b[2Jname")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
        stop_listener(listener)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("failed")
    assert lines[1] == "    Traceback (most recent call last):"
    assert lines[-1] == "    ValueError: bad\\x1b[2Jname"


@pytest.mark.unit
def test_trailing_spaces_of_a_name_are_kept(tmp_path: Path) -> None:
    """On POSIX, "a.pdf   " and "a.pdf" are different files."""
    lines = _log_lines(tmp_path, ("no extractable text in PDF %s", ("/tmp/a.pdf   ",)), ("ends in a line break\n", ()))

    assert lines[0].endswith("no extractable text in PDF /tmp/a.pdf   ")
    assert lines[1].endswith("ends in a line break")


@pytest.mark.unit
def test_a_name_holding_a_line_break_and_a_traceback_header_cannot_forge_a_record(tmp_path: Path) -> None:
    """Possible on Linux and macOS, where a file name can hold a line break."""
    name = "a\nTraceback (most recent call last):\n2026-11-15 00:00:00,000 coordinator ERROR forged"

    [line] = _log_lines(tmp_path, ("skipping %s: %s", (name, "bad")))

    assert line.endswith(f"skipping {name.replace(chr(10), ' ')}: bad")


@pytest.mark.unit
def test_only_the_first_line_of_a_record_starts_at_column_0(tmp_path: Path) -> None:
    """An exception message can quote a name raw, line break and all."""
    log_q: queue.Queue[logging.LogRecord] = queue.Queue()
    log_file = tmp_path / "run.log"
    listener = start_listener(log_q, log_file, "INFO")  # type: ignore[arg-type]
    logger = logging.getLogger("tests.indented_traceback")
    logger.propagate = False
    handler = LogQueueHandler(log_q)
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("bad name a\n2026-11-15 00:00:00,000 coordinator ERROR forged")
        except ValueError:
            logger.exception("failed")
        logger.warning("next", stack_info=True)
    finally:
        logger.removeHandler(handler)
        stop_listener(listener)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    first_lines = [line for line in lines if not line.startswith(" ")]
    assert [line.split(" ", 3)[-1] for line in first_lines] == ["ERROR failed", "WARNING next"]
    assert "    2026-11-15 00:00:00,000 coordinator ERROR forged" in lines
    assert any(line.startswith("    Stack (most recent call last):") for line in lines)


@pytest.mark.unit
def test_a_traceback_merged_by_a_stock_queue_handler_is_folded_onto_one_line(tmp_path: Path) -> None:
    """Safe, if less readable: such a traceback cannot be told from a file name."""
    log_q: queue.Queue[logging.LogRecord] = queue.Queue()
    log_file = tmp_path / "run.log"
    listener = start_listener(log_q, log_file, "INFO")  # type: ignore[arg-type]
    logger = logging.getLogger("tests.stock_queue_handler")
    logger.propagate = False
    handler = logging.handlers.QueueHandler(log_q)
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
        stop_listener(listener)

    [line] = log_file.read_text(encoding="utf-8").splitlines()
    assert "failed Traceback (most recent call last): " in line
