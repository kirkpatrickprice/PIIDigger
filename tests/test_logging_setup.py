"""Unit tests for third-party library log routing."""

from __future__ import annotations

import logging
import logging.handlers
import queue
from collections.abc import Iterator

import pytest

from piidigger.orchestration.logging_setup import route_library_logs, stop_library_log_routing


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
