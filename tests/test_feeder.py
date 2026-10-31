"""Unit tests for TaskFeeder — the window of tasks the coordinator keeps on the task queue."""

from __future__ import annotations

import pytest

from piidigger.models.tasks import Task, TaskType
from piidigger.orchestration.feeder import TaskFeeder


def _tasks(n: int) -> list[Task]:
    return [Task(task_type=TaskType.NOOP) for _ in range(n)]


@pytest.mark.unit
def test_puts_no_more_than_the_window() -> None:
    sent: list[Task] = []
    feeder = TaskFeeder(sent.append, window=3)
    tasks = _tasks(10)

    for task in tasks:
        feeder.submit(task)

    assert sent == tasks[:3]
    assert feeder.in_flight == 3
    assert len(feeder) == 7


@pytest.mark.unit
def test_started_frees_a_slot_for_the_next_task() -> None:
    sent: list[Task] = []
    feeder = TaskFeeder(sent.append, window=2)
    tasks = _tasks(4)
    for task in tasks:
        feeder.submit(task)

    feeder.started(tasks[0].task_id)

    assert sent == tasks[:3]
    assert len(feeder) == 1


@pytest.mark.unit
def test_started_for_an_unknown_task_changes_nothing() -> None:
    sent: list[Task] = []
    feeder = TaskFeeder(sent.append, window=1)
    tasks = _tasks(2)
    for task in tasks:
        feeder.submit(task)

    feeder.started("not-a-task")

    assert sent == tasks[:1]
    assert feeder.in_flight == 1


@pytest.mark.unit
def test_resubmitting_a_lost_task_frees_its_slot() -> None:
    sent: list[Task] = []
    feeder = TaskFeeder(sent.append, window=1)
    lost, waiting = _tasks(2)
    feeder.submit(lost)
    feeder.submit(waiting)

    feeder.submit(lost)

    assert sent == [lost, waiting]
    assert feeder.in_flight == 1
    assert len(feeder) == 1


@pytest.mark.unit
def test_resubmitting_a_waiting_task_replaces_it_in_place() -> None:
    """A task re-dispatched while still waiting must not be queued twice."""
    sent: list[Task] = []
    feeder = TaskFeeder(sent.append, window=1)
    first, second, third = _tasks(3)
    for task in (first, second, third):
        feeder.submit(task)
    trimmed = second.model_copy(update={"timeout_seconds": 99})

    feeder.submit(trimmed)

    assert len(feeder) == 2
    assert feeder.is_waiting(second.task_id)
    feeder.started(first.task_id)
    assert sent[-1] is trimmed, "the replacement keeps its place in line"


@pytest.mark.unit
def test_release_frees_the_slot_of_a_task_given_up_on() -> None:
    sent: list[Task] = []
    feeder = TaskFeeder(sent.append, window=1)
    abandoned, waiting = _tasks(2)
    feeder.submit(abandoned)
    feeder.submit(waiting)

    feeder.release(abandoned.task_id)

    assert sent == [abandoned, waiting]
    assert feeder.in_flight == 1
    assert len(feeder) == 0


@pytest.mark.unit
def test_window_is_at_least_one() -> None:
    sent: list[Task] = []
    feeder = TaskFeeder(sent.append, window=0)

    feeder.submit(Task(task_type=TaskType.NOOP))

    assert len(sent) == 1
