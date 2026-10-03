"""TaskProgress: the worker-side reporter and the coordinator's handling of it.

Runs in-process with a fake clock and list-backed queues, so nothing spawns
and nothing sleeps.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from piidigger.models.results import ResultRecord
from piidigger.models.tasks import Task, TaskProgress, TaskResult, TaskType
from piidigger.orchestration.coordinator import _handle_message
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.registry import TaskRegistry
from piidigger.orchestration.worker._reporter import ProgressReporter
from tests._fakes import FakeClock

_LOG = logging.getLogger("tests.task_progress")
_PID = 4242


class _Sink:
    def __init__(self) -> None:
        self.records: list[ResultRecord] = []

    def write(self, record: ResultRecord) -> None:
        self.records.append(record)


def _finding(member: str) -> dict[str, Any]:
    return ResultRecord(
        source_path="archive.tar.gz",
        source_member_path=member,
        source_depth=1,
        source_container_type="tar",
        handler="pan",
        matches={"pan": ["4111111111111111"]},
    ).model_dump()


def _setup(*items: str) -> tuple[TaskRegistry, Task, _Sink, ProgressDisplay]:
    registry = TaskRegistry(lambda _task: None, clock=FakeClock())
    task = Task(task_type=TaskType.SCAN_ARCHIVE_MEMBERS, payload={"archive_path": "archive.tar.gz"}, items=items)
    registry.enqueue(task)
    registry.record_start(task.task_id, _PID)
    return registry, task, _Sink(), ProgressDisplay()


def _done(task: Task, member: str, counters: dict[str, int] | None = None) -> TaskProgress:
    return TaskProgress(
        task_id=task.task_id,
        worker_pid=_PID,
        event="item_done",
        item=member,
        findings=[_finding(member)],
        counters=counters or {"files_scanned": 1},
    )


# ---------------------------------------------------------------------------
# ProgressReporter
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_alive_is_rate_limited() -> None:
    sent: list[TaskProgress] = []
    clock = FakeClock()
    reporter = ProgressReporter(sent.append, "t1", clock=clock, alive_interval=1.0)

    reporter.alive()  # less than a second since creation
    clock.advance(1.0)
    reporter.alive()
    reporter.alive()  # immediately again
    clock.advance(0.5)
    reporter.alive()

    assert [m.event for m in sent] == ["alive"]


@pytest.mark.unit
def test_item_events_always_send_and_restart_the_alive_interval() -> None:
    sent: list[TaskProgress] = []
    clock = FakeClock()
    reporter = ProgressReporter(sent.append, "t1", clock=clock, alive_interval=1.0)

    clock.advance(5.0)
    reporter.item_started("a")
    reporter.item_done("a", [], {"files_scanned": 1})
    reporter.alive()  # suppressed: item_done just went out

    assert [(m.event, m.item) for m in sent] == [("item_started", "a"), ("item_done", "a")]
    assert sent[1].counters == {"files_scanned": 1}


# ---------------------------------------------------------------------------
# Coordinator handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_item_done_writes_its_findings_and_counters() -> None:
    registry, task, sink, progress = _setup("a", "b")

    _handle_message(_done(task, "a"), registry, [sink], progress, _LOG)

    assert [r.source_member_path for r in sink.records] == ["a"]
    assert progress._counters["files_scanned"] == 1


@pytest.mark.unit
def test_duplicate_item_done_is_dropped() -> None:
    """Two copies of a batch both report a member; its findings are written once."""
    registry, task, sink, progress = _setup("a")

    _handle_message(_done(task, "a"), registry, [sink], progress, _LOG)
    _handle_message(_done(task, "a"), registry, [sink], progress, _LOG)

    assert len(sink.records) == 1
    assert progress._counters["files_scanned"] == 1


@pytest.mark.unit
def test_alive_and_item_started_write_nothing() -> None:
    registry, task, sink, progress = _setup("a")

    for event in ("alive", "item_started"):
        message = TaskProgress(task_id=task.task_id, worker_pid=_PID, event=event, item="a")  # type: ignore[arg-type]
        _handle_message(message, registry, [sink], progress, _LOG)

    assert sink.records == []
    assert task.task_id in registry


@pytest.mark.unit
def test_batch_result_counts_unreported_members_as_failed() -> None:
    """An archive that breaks partway: the members never reported were not scanned."""
    registry, task, sink, progress = _setup("a", "b", "c")
    _handle_message(_done(task, "a"), registry, [sink], progress, _LOG)

    result = TaskResult(task_id=task.task_id, task_type=task.task_type, status="error", error_message="corrupt")
    _handle_message(result, registry, [sink], progress, _LOG)

    assert len(registry) == 0
    assert progress.incomplete.failed == 2


# ---------------------------------------------------------------------------
# Review fixes: progress only on real reads; a batch error fails one member
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_read_at_end_of_file_is_not_progress(tmp_path: Path) -> None:
    """A decoder spinning at EOF reads 0 bytes; that must not keep its task alive."""
    from piidigger.archivehandlers._progress_io import _ProgressRaw

    path = tmp_path / "data.bin"
    path.write_bytes(b"0123456789")
    calls: list[int] = []
    raw = _ProgressRaw(path, lambda: calls.append(1))
    buffer = bytearray(64)

    assert raw.readinto(buffer) == 10
    for _ in range(5):
        assert raw.readinto(buffer) == 0
    raw.close()

    assert calls == [1]


def _batch_registry(*items: str) -> tuple[TaskRegistry, list[Task], Task]:
    dispatched: list[Task] = []
    registry = TaskRegistry(dispatched.append, clock=FakeClock())
    task = Task(task_type=TaskType.SCAN_ARCHIVE_MEMBERS, payload={"archive_path": "archive.7z"}, items=items)
    registry.enqueue(task)
    registry.record_start(task.task_id, _PID)
    return registry, dispatched, task


def _error(task: Task, pid: int = _PID) -> TaskResult:
    return TaskResult(
        task_id=task.task_id, task_type=task.task_type, status="error", error_message="CRC error", worker_pid=pid
    )


@pytest.mark.unit
def test_batch_error_on_one_member_fails_only_that_member() -> None:
    registry, dispatched, task = _batch_registry("a", "b", "c")
    sink, progress = _Sink(), ProgressDisplay()
    _handle_message(_done(task, "a"), registry, [sink], progress, _LOG)
    started = TaskProgress(task_id=task.task_id, worker_pid=_PID, event="item_started", item="b")
    _handle_message(started, registry, [sink], progress, _LOG)

    _handle_message(_error(task), registry, [sink], progress, _LOG)

    assert task.task_id in registry, "the batch carries on"
    assert dispatched[-1].items == ("c",), "requeued with only the members not yet tried"
    assert progress.incomplete.failed == 1
    assert [r.source_member_path for r in sink.records] == ["a"]


@pytest.mark.unit
def test_batch_error_on_its_last_member_finishes_the_batch() -> None:
    registry, dispatched, task = _batch_registry("a")
    progress = ProgressDisplay()
    started = TaskProgress(task_id=task.task_id, worker_pid=_PID, event="item_started", item="a")
    _handle_message(started, registry, [], progress, _LOG)

    _handle_message(_error(task), registry, [], progress, _LOG)

    assert len(registry) == 0
    assert dispatched == [task], "nothing left to requeue"
    assert progress.incomplete.failed == 1
    assert registry.count_abandoned("crashed") == registry.count_abandoned("timed_out") == 0


@pytest.mark.unit
def test_batch_error_with_no_member_in_progress_fails_what_is_left() -> None:
    """Unreadable before reaching any member: nothing to blame, so the rest is not scanned."""
    registry, dispatched, task = _batch_registry("a", "b")
    progress = ProgressDisplay()

    _handle_message(_error(task), registry, [], progress, _LOG)

    assert len(registry) == 0
    assert dispatched == [task]
    assert progress.incomplete.failed == 2


@pytest.mark.unit
def test_batch_error_from_a_superseded_copy_is_not_requeued() -> None:
    """Only the worker holding the batch can blame a member."""
    registry, dispatched, task = _batch_registry("a", "b")
    started = TaskProgress(task_id=task.task_id, worker_pid=_PID, event="item_started", item="a")
    _handle_message(started, registry, [], ProgressDisplay(), _LOG)

    _handle_message(_error(task, pid=_PID + 1), registry, [], ProgressDisplay(), _LOG)

    assert dispatched == [task]
