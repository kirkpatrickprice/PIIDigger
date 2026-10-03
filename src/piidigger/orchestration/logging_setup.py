from __future__ import annotations

import logging
import logging.handlers
import multiprocessing as mp
from pathlib import Path
from typing import Any

# How long stop_listener() waits for queued log records to be written out.
_LISTENER_STOP_TIMEOUT: float = 5.0


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


def _route_to_queue(logger: logging.Logger, log_queue: mp.Queue[Any]) -> None:
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
    logger.addHandler(logging.handlers.QueueHandler(log_queue))


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
    handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
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
