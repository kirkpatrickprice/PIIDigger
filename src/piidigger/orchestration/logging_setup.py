from __future__ import annotations

import copy
import logging
import logging.handlers
import multiprocessing as mp
from pathlib import Path
from typing import Any

from piidigger.orchestration.escaping import UTF8_ERRORS

# How long stop_listener() waits for queued log records to be written out.
_LISTENER_STOP_TIMEOUT: float = 5.0

# Third-party loggers held above route_library_logs()' WARNING floor.
# pypdf logs a WARNING for many recoverable defects in malformed PDFs (bad
# xref offsets, wrong object pointers), which would flood the log on a large
# scan.  This is the level pdf.py used to set for itself.
_LIBRARY_LOG_LEVELS: dict[str, int] = {"pypdf": logging.ERROR}


def build_worker_logger(log_queue: mp.Queue[Any], name: str = "worker") -> logging.Logger:
    """Return a logger that sends all records to log_queue via QueueHandler.

    Call this inside each worker process — never pass a Logger across the
    spawn boundary.  Idempotent: re-calling with the same name and queue does
    not add a duplicate handler, and re-calling with a different queue moves the
    logger to it.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    _route_to_queue(logger, log_queue)
    logger.propagate = False
    return logger


def route_library_logs(log_queue: mp.Queue[Any]) -> None:
    """Send third-party library log records at WARNING and above to log_queue.

    Library loggers (pypdf, openpyxl, py7zr…) propagate to the root logger.
    With no handler there, Python's last-resort handler prints them to stderr:
    they corrupt the rich.Live display and never reach the log file.  Our own
    loggers set propagate=False, so their records are not duplicated.

    Library-specific levels live here too, so file handlers never configure
    logging themselves.  Call once in each process, after build_worker_logger().
    Idempotent, like _route_to_queue().
    """
    _route_to_queue(logging.getLogger(), log_queue, level=logging.WARNING)
    for name, level in _LIBRARY_LOG_LEVELS.items():
        logging.getLogger(name).setLevel(level)


def stop_library_log_routing() -> None:
    """Remove the root logger's QueueHandler added by route_library_logs().

    Call in the coordinator process once the listener has stopped.  Otherwise a
    later record would go to a queue nobody drains.  Its feeder thread could
    then block on a full pipe and stall interpreter exit.
    """
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, logging.handlers.QueueHandler)]:
        root.removeHandler(handler)


def _route_to_queue(logger: logging.Logger, log_queue: mp.Queue[Any], *, level: int = logging.NOTSET) -> None:
    """Make log_queue the logger's only QueueHandler target.

    Loggers are process-wide singletons keyed by name.  A check for "any
    QueueHandler" would keep a logger bound to the first queue it ever saw.  A
    later run in the same process — a second run_scan, or the next test — would
    then send its records to a queue nobody is listening on, and they would be
    lost without any error.
    """
    handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.QueueHandler)]
    if any(h.queue is log_queue for h in handlers):
        return
    for handler in handlers:
        logger.removeHandler(handler)
    handler = LogQueueHandler(log_queue)
    handler.setLevel(level)
    logger.addHandler(handler)


class LogQueueHandler(logging.handlers.QueueHandler):
    """A QueueHandler that sends a record's traceback apart from its message.

    The stock prepare() appends the traceback to the message text.  The log's
    formatter could then find it only by searching that text, and a file name
    logged in the message can imitate a traceback.  Here the traceback crosses
    the queue in exc_text, a string, so the formatter knows which part is which.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        message = record.getMessage()
        exc_text = record.exc_text
        if record.exc_info and not exc_text:
            exc_text = logging.Formatter().formatException(record.exc_info)
        record = copy.copy(record)
        record.message = message
        record.msg = message
        record.args = None
        record.exc_info = None  # a traceback object does not pickle
        record.exc_text = exc_text
        return record


# Each line of a traceback or stack in the log starts with this, so only the
# first line of a record starts at column 0.  A forged record cannot either.
_TRACE_INDENT = "    "


def _escape_char(char: str) -> str:
    """A character as an escape: \\xNN, \\uNNNN or \\UNNNNNNNN.

    As repr() writes it, except that \\xNN is used below 0x80 only.  A byte
    that is not UTF-8 is always 0x80 or above, and the log writes it as \\xNN,
    so from 0x80 on a character gets \\uNNNN where repr() would write \\xNN.
    """
    code = ord(char)
    if code < 0x80:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def _escape_unprintable(text: str) -> str:
    """text with each character repr() would escape written as an escape.

    A file name can hold any character.  Written raw, a carriage return or an
    escape sequence rewrites what a terminal shows, U+2028 splits the line for
    str.splitlines(), and U+202E reverses the rest of it on screen.  Tab stays,
    and so do surrogates: the file handler writes those as \\xNN, the byte of a
    POSIX name that is not UTF-8.
    """
    if text.isprintable():
        return text
    return "".join(
        char if char.isprintable() or char == "\t" or "\ud800" <= char <= "\udfff" else _escape_char(char)
        for char in text
    )


class _SingleLineFormatter(logging.Formatter):
    """Writes each record's message on one line, so line-based tools read the log right.

    Some exception text spans several lines; tarfile, for one, lists every
    compression method it tried.  The message's line breaks become spaces.  A
    traceback or stack follows on lines of its own, each indented, so only a
    record's first line starts at column 0.  LogQueueHandler sends the
    traceback apart from the message, so no text in the message, a file name
    included, can pass for one.

    Paths are logged as they are, not as reprs.  So any other character that
    repr() would escape is written as an escape (see _escape_char), and a file
    name cannot rewrite the line.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        lines = [self.formatMessage(record)]
        exc_text = record.exc_text
        if record.exc_info and not exc_text:
            exc_text = self.formatException(record.exc_info)
        for trace in (exc_text, record.stack_info):
            if trace:
                lines.extend(_TRACE_INDENT + _escape_unprintable(line) for line in trace.rstrip("\n").split("\n"))
        return "\n".join(lines)

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802 — logging.Formatter API
        text = super().formatMessage(record)
        # Only line breaks are stripped: trailing spaces can be part of a name.
        return _escape_unprintable(text.rstrip("\r\n").replace("\r\n", " ").replace("\n", " "))


def start_listener(
    log_queue: mp.Queue[Any],
    log_file: Path,
    log_level: str,
) -> logging.handlers.QueueListener:
    """Start a QueueListener that drains log_queue to log_file.

    Must be started before any worker is launched and stopped after all
    workers have joined, so no log records are lost.

    The QueueListener typeshed signature expects queue.Queue or SimpleQueue;
    mp.Queue is duck-type compatible (same get/put interface) so the
    type: ignore suppresses a false-positive rather than a real mismatch.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    # mode="w": each run starts a fresh log file.
    # encoding="utf-8": FileHandler's default encoding is the platform's
    # preferred locale encoding.  On Windows that is not UTF-8, so the first
    # non-ASCII character logged (e.g. a path) raised UnicodeEncodeError in
    # the listener thread, dropping the record and corrupting the rich.Live
    # display.
    # errors=UTF8_ERRORS: a POSIX file name that is not valid UTF-8 arrives as
    # surrogate escapes, which strict UTF-8 cannot encode.  logging would drop
    # that record, and with it the only report of that file.
    handler = logging.FileHandler(log_file, mode="w", encoding="utf-8", errors=UTF8_ERRORS)
    handler.setLevel(level)
    handler.setFormatter(_SingleLineFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    listener = logging.handlers.QueueListener(
        log_queue,
        handler,
        respect_handler_level=True,
    )
    listener.start()
    return listener


def stop_listener(listener: logging.handlers.QueueListener, timeout: float = _LISTENER_STOP_TIMEOUT) -> bool:
    """Stop the QueueListener, waiting at most timeout seconds.  True if it stopped.

    QueueListener.stop() joins its thread with no timeout.  If a worker was
    killed partway through writing a log record, the listener thread blocks
    forever reading the half-written record, and so would stop().  Teardown
    would then hang after the scan had finished.  Here we give up after timeout
    instead.  The listener thread is a daemon, so it cannot keep the process
    alive.

    Safe to call more than once: after a stop, or after giving up, later calls
    return at once.
    """
    thread = listener._thread
    if thread is None:
        return True
    listener.enqueue_sentinel()
    thread.join(timeout)
    listener._thread = None  # stopped, or given up on: either way, done waiting
    return not thread.is_alive()


def log_after_stop(listener: logging.handlers.QueueListener, name: str, level: int, message: str) -> None:
    """Write one record straight to a stopped listener's handlers, at their own levels.

    The coordinator stops the listener during its teardown, before run_scan()
    knows its exit code.  The handlers stay open, so this is how that last line
    still reaches the log file.
    """
    listener.handle(logging.LogRecord(name, level, __file__, 0, message, None, None))


def _pkg_from_path(filename: str) -> str | None:
    """Return the top-level package name for a file inside site-packages, or None."""
    try:
        parts = Path(filename).parts
        for i, part in enumerate(parts):
            if part in ("site-packages", "dist-packages") and i + 1 < len(parts):
                return parts[i + 1]
    except Exception:  # noqa: BLE001, S110
        pass
    return None


def setup_warning_capture(log_queue: mp.Queue[Any]) -> None:
    """Redirect Python warnings to log_queue instead of stderr.

    Installs a custom warnings.showwarning (once per process) that:
      - Routes all warnings to the py.warnings logger → log file instead of
        stderr, preventing corruption of Rich's Live display
      - Appends [source: <pkg>] when the warning originates from a
        third-party package in site-packages (e.g. xlrd, pypdf)
      - For <unknown> filenames, walks the call stack to infer the source

    Idempotent: re-calling only updates the QueueHandler, not the hook.
    """
    import warnings as _warnings_mod

    if not getattr(_warnings_mod.showwarning, "__piidigger__", False):
        _orig = _warnings_mod.showwarning

        def _capture(
            message: Any,
            category: type,
            filename: str,
            lineno: int,
            file: Any = None,
            line: str | None = None,
        ) -> None:
            if file is not None:
                _orig(message, category, filename, lineno, file, line)
                return
            pkg = _pkg_from_path(filename)
            if pkg is None:
                import traceback

                for frame in traceback.extract_stack():
                    pkg = _pkg_from_path(frame.filename)
                    if pkg:
                        break
            suffix = f" [source: {pkg}]" if pkg else ""
            logging.getLogger("py.warnings").warning(
                "%s %s:%d: %s: %s",
                suffix,
                filename,
                lineno,
                category.__name__,
                str(message),
            )

        _capture.__piidigger__ = True  # type: ignore[attr-defined]
        _warnings_mod.showwarning = _capture

    warn_logger = logging.getLogger("py.warnings")
    warn_logger.setLevel(logging.WARNING)
    _route_to_queue(warn_logger, log_queue)
    warn_logger.propagate = False
