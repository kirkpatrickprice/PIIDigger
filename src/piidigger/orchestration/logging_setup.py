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
