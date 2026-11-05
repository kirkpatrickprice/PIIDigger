"""Unit tests for piidigger.models.tasks — Phase 1."""

from __future__ import annotations

import pickle

import pytest
from pydantic import ValidationError

from piidigger.models.tasks import (
    SHUTDOWN,
    ShutdownSentinel,
    Task,
    TaskResult,
    TaskStarted,
    TaskType,
)

# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_default_fields():
    task = Task(task_type=TaskType.NOOP)
    assert task.task_type is TaskType.NOOP
    assert isinstance(task.task_id, str) and len(task.task_id) == 32
    assert task.timeout_seconds == 30
    assert task.payload == {}


@pytest.mark.unit
def test_task_custom_fields():
    task = Task(task_type=TaskType.ENUM_DIR, payload={"path": "/tmp"}, timeout_seconds=60)
    assert task.task_type is TaskType.ENUM_DIR
    assert task.payload == {"path": "/tmp"}
    assert task.timeout_seconds == 60


@pytest.mark.unit
def test_task_unique_ids():
    a = Task(task_type=TaskType.NOOP)
    b = Task(task_type=TaskType.NOOP)
    assert a.task_id != b.task_id


@pytest.mark.unit
@pytest.mark.parametrize("bad_timeout", [0, 601, -1])
def test_task_rejects_invalid_timeout(bad_timeout: int):
    with pytest.raises(ValidationError):
        Task(task_type=TaskType.NOOP, timeout_seconds=bad_timeout)


@pytest.mark.unit
def test_task_rejects_unknown_task_type():
    with pytest.raises(ValidationError):
        Task(task_type="not_a_real_type")  # type: ignore[arg-type]


@pytest.mark.unit
def test_task_is_frozen():
    task = Task(task_type=TaskType.NOOP)
    with pytest.raises((TypeError, ValidationError)):
        task.timeout_seconds = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Task pickling (Windows spawn requires this)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_round_trips_pickle():
    original = Task(task_type=TaskType.SCAN_FILE, payload={"path": "/a/b.txt"}, timeout_seconds=45)
    restored: Task = pickle.loads(pickle.dumps(original))
    assert restored.task_id == original.task_id
    assert restored.task_type is TaskType.SCAN_FILE
    assert restored.payload == {"path": "/a/b.txt"}
    assert restored.timeout_seconds == 45


# ---------------------------------------------------------------------------
# TaskResult validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_result_valid_statuses():
    task = Task(task_type=TaskType.NOOP)
    for status in ("ok", "timeout", "error"):
        result = TaskResult(task_id=task.task_id, task_type=task.task_type, status=status)  # type: ignore[arg-type]
        assert result.status == status


@pytest.mark.unit
def test_task_result_rejects_invalid_status():
    task = Task(task_type=TaskType.NOOP)
    with pytest.raises(ValidationError):
        TaskResult(task_id=task.task_id, task_type=task.task_type, status="unknown")  # type: ignore[arg-type]


@pytest.mark.unit
def test_task_result_default_fields():
    task = Task(task_type=TaskType.NOOP)
    result = TaskResult(task_id=task.task_id, task_type=task.task_type, status="ok")
    assert result.new_tasks == []
    assert result.findings == []
    assert result.counters == {}
    assert result.error_message is None
    assert result.duration_seconds == 0.0
    assert result.worker_pid is None


@pytest.mark.unit
def test_task_result_rejects_negative_duration():
    task = Task(task_type=TaskType.NOOP)
    with pytest.raises(ValidationError):
        TaskResult(task_id=task.task_id, task_type=task.task_type, status="ok", duration_seconds=-1.0)


# ---------------------------------------------------------------------------
# TaskStarted
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_task_started_fields():
    ts = TaskStarted(task_id="abc123", worker_pid=9999)
    assert ts.task_id == "abc123"
    assert ts.worker_pid == 9999


@pytest.mark.unit
def test_task_started_round_trips_pickle():
    original = TaskStarted(task_id="deadbeef", worker_pid=1234)
    restored: TaskStarted = pickle.loads(pickle.dumps(original))
    assert restored.task_id == original.task_id
    assert restored.worker_pid == original.worker_pid


# ---------------------------------------------------------------------------
# ShutdownSentinel
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_shutdown_sentinel_isinstance_survives_pickle():
    """isinstance check must still work after unpickling (spawn boundary)."""
    restored: object = pickle.loads(pickle.dumps(SHUTDOWN))
    assert isinstance(restored, ShutdownSentinel)


@pytest.mark.unit
def test_shutdown_constant_is_sentinel():
    assert isinstance(SHUTDOWN, ShutdownSentinel)
