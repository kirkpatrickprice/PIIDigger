from __future__ import annotations

import logging
import logging.handlers
import multiprocessing as mp
from pathlib import Path
from typing import Any


def build_worker_logger(log_queue: mp.Queue[Any], name: str = "worker") -> logging.Logger:
    """Return a logger that sends all records to log_queue via QueueHandler.

    Call this inside each worker process — never pass a Logger across the
    spawn boundary.  Idempotent: re-calling with the same name does not
    add duplicate handlers.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if not any(isinstance(h, logging.handlers.QueueHandler) for h in logger.handlers):
        logger.addHandler(logging.handlers.QueueHandler(log_queue))
    logger.propagate = False
    return logger


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
    handler = logging.FileHandler(log_file)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    listener = logging.handlers.QueueListener(
        log_queue,
        handler,
        respect_handler_level=True,
    )
    listener.start()
    return listener


def stop_listener(listener: logging.handlers.QueueListener) -> None:
    """Stop the QueueListener; blocks until all queued records are written."""
    listener.stop()


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
    if not any(isinstance(h, logging.handlers.QueueHandler) for h in warn_logger.handlers):
        warn_logger.addHandler(logging.handlers.QueueHandler(log_queue))
    warn_logger.propagate = False
