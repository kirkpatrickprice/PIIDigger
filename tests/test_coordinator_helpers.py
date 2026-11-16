"""Unit tests for coordinator module-level helper functions.

These functions live in coordinator.py but are directly importable for isolated
unit testing without spawning any processes.
"""

from __future__ import annotations

import pytest

from piidigger.orchestration.coordinator import (
    FullDiskAccessNotice,
    _denied_path,
    _findings_summary,
    _short_error,
    _truncate_path,
)

# ---------------------------------------------------------------------------
# _truncate_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_truncate_path_short_path_unchanged() -> None:
    path = "C:/short/path.txt"
    assert _truncate_path(path) == path


@pytest.mark.unit
def test_truncate_path_exactly_max_unchanged() -> None:
    path = "a" * 60
    assert _truncate_path(path) == path


@pytest.mark.unit
def test_truncate_path_long_path_with_backslash() -> None:
    path = "C:\\Users\\Randy\\Documents\\very_long_folder_name_here\\subfolder\\filename.txt"
    result = _truncate_path(path, max_len=60)
    assert len(result) <= 60
    assert "filename.txt" in result
    assert "..." in result


@pytest.mark.unit
def test_truncate_path_long_path_with_forward_slash() -> None:
    path = "/home/randy/very_long_folder/another_long_folder/afile.txt"
    result = _truncate_path(path, max_len=40)
    assert len(result) <= 40
    assert "afile.txt" in result
    assert "..." in result


@pytest.mark.unit
def test_truncate_path_no_separator_falls_back() -> None:
    path = "a" * 80
    result = _truncate_path(path, max_len=60)
    assert len(result) == 60
    assert result.endswith("...")


# ---------------------------------------------------------------------------
# _denied_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_denied_path_extracts_path() -> None:
    msg = "[Errno 13] Permission denied: '/etc/shadow'"
    assert _denied_path(msg) == "/etc/shadow"


@pytest.mark.unit
def test_denied_path_windows_style() -> None:
    msg = "[WinError 5] Access is denied: 'C:\\Windows\\System32\\protected'"
    assert _denied_path(msg) == "C:\\Windows\\System32\\protected"


@pytest.mark.unit
def test_denied_path_no_colon_quote_separator() -> None:
    msg = "just some error without a path"
    assert _denied_path(msg) == "just some error without a path"


# ---------------------------------------------------------------------------
# _short_error
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_short_error_strips_path_suffix() -> None:
    msg = "[Errno 13] Permission denied: '/etc/shadow'"
    assert _short_error(msg) == "[Errno 13] Permission denied"


@pytest.mark.unit
def test_short_error_truncates_at_80() -> None:
    msg = "x" * 100
    result = _short_error(msg)
    assert len(result) == 80


@pytest.mark.unit
def test_short_error_uses_first_line_only() -> None:
    msg = "first line\nsecond line\nthird line"
    assert _short_error(msg) == "first line"


@pytest.mark.unit
def test_short_error_short_message_unchanged() -> None:
    msg = "File not found"
    assert _short_error(msg) == "File not found"


# ---------------------------------------------------------------------------
# _findings_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_findings_summary_empty_list() -> None:
    assert _findings_summary([]) == ""


@pytest.mark.unit
def test_findings_summary_single_finding() -> None:
    findings = [
        {
            "source_path": "testdata/pan/sample.txt",
            "handler": "pan",
            "matches": {"pan": ["4111111111111111"]},
        }
    ]
    result = _findings_summary(findings)
    assert "sample.txt" in result
    assert "PAN: 1" in result


@pytest.mark.unit
def test_findings_summary_multiple_handlers_sorted() -> None:
    findings = [
        {
            "source_path": "file.txt",
            "handler": "pan",
            "matches": {"pan": ["4111111111111111", "5555555555554444"]},
        },
        {
            "source_path": "file.txt",
            "handler": "ssn",
            "matches": {"ssn": ["123-45-6789"]},
        },
    ]
    result = _findings_summary(findings)
    # Counts are summed per handler, output is alphabetically sorted
    assert "PAN: 2" in result
    assert "SSN: 1" in result
    assert result.index("PAN") < result.index("SSN")


@pytest.mark.unit
def test_findings_summary_missing_keys_use_defaults() -> None:
    # Findings with no 'handler' or 'matches' should not raise
    findings = [{"source_path": "file.txt"}]
    result = _findings_summary(findings)
    assert "file.txt" in result
    assert "?: 0" in result


# ---------------------------------------------------------------------------
# Log lines name paths as written, not as Python reprs
# ---------------------------------------------------------------------------

_WINDOWS_PATH = "C:\\Users\\x\\notes.txt"


@pytest.mark.unit
def test_error_line_names_a_windows_path_with_single_backslashes(caplog: pytest.LogCaptureFixture) -> None:
    """A repr doubled every backslash, so the logged path didn't match the one on disk."""
    import logging

    from piidigger.models.tasks import Task, TaskResult, TaskType
    from piidigger.orchestration.coordinator import _handle_result
    from piidigger.orchestration.progress import ProgressDisplay
    from piidigger.orchestration.registry import TaskRegistry

    logger = logging.getLogger("tests.coordinator_helpers.path_format")
    registry = TaskRegistry(lambda _task: None)
    task = Task(task_type=TaskType.SCAN_FILE, payload={"file_path": _WINDOWS_PATH})
    registry.enqueue(task)
    result = TaskResult(task_id=task.task_id, task_type=TaskType.SCAN_FILE, status="error", error_message="boom")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        _handle_result(result, registry, [], ProgressDisplay(), logger)

    assert [r.getMessage() for r in caplog.records] == [f"[scan_file] path={_WINDOWS_PATH}: boom"]


def _error_result_log(
    caplog: pytest.LogCaptureFixture, *, message: str, error_kind: str | None, items: tuple[str, ...] = ()
) -> tuple[list[tuple[int, str]], dict[str, int], list[tuple[str, str]]]:
    """Feed one failed result to _handle_result: its log lines, the summary counts, and the events.

    An ENUM_DIR task, or a SCAN_ARCHIVE_MEMBERS batch of items none of which started.
    """
    import logging

    from piidigger.models.tasks import Task, TaskResult, TaskType
    from piidigger.orchestration.coordinator import _handle_result
    from piidigger.orchestration.progress import ProgressDisplay
    from piidigger.orchestration.registry import TaskRegistry

    logger = logging.getLogger("tests.coordinator_helpers.error_kind")
    registry = TaskRegistry(lambda _task: None)
    if items:
        task = Task(task_type=TaskType.SCAN_ARCHIVE_MEMBERS, payload={"archive_path": "/Users/x/a.zip"}, items=items)
    else:
        task = Task(task_type=TaskType.ENUM_DIR, payload={"path": "/Users/x/Library/Mail"})
    registry.enqueue(task)
    result = TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="error",
        error_message=message,
        error_kind=error_kind,  # type: ignore[arg-type]
    )
    progress = ProgressDisplay()

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        _handle_result(result, registry, [], progress, logger)

    work = progress.incomplete
    counts = {
        "failed": work.failed,
        "access_denied": work.access_denied,
        "files_not_scanned": progress._counters.get("files_not_scanned", 0),
    }
    return [(r.levelno, r.getMessage()) for r in caplog.records], counts, list(progress._events)


@pytest.mark.unit
def test_access_denied_kind_is_a_warning_counted_apart(caplog: pytest.LogCaptureFixture) -> None:
    """macOS privacy controls fail with EPERM, whose message no phrase list matched, so it logged as an error."""
    import logging

    message = "[Errno 1] Operation not permitted: '/Users/x/Library/Mail'"
    lines, counts, events = _error_result_log(caplog, message=message, error_kind="access_denied")

    assert lines == [(logging.WARNING, f"[enum_dir] path=/Users/x/Library/Mail: access denied: {message}")]
    assert counts == {"failed": 0, "access_denied": 1, "files_not_scanned": 0}
    assert events == [("WARNING", "Access denied: /Users/x/Library/Mail")]


@pytest.mark.unit
def test_archive_batch_refused_as_a_whole_counts_once_as_access_denied(caplog: pytest.LogCaptureFixture) -> None:
    """One log line, one count: the row's search hint must find as many lines as the row counts."""
    import logging

    message = "[Errno 13] Permission denied: '/Users/x/a.zip'"
    lines, counts, _ = _error_result_log(caplog, message=message, error_kind="access_denied", items=("a.txt", "b.txt"))

    assert [level for level, _ in lines] == [logging.WARNING]
    assert counts == {"failed": 0, "access_denied": 1, "files_not_scanned": 2}, "its members still count as not scanned"


@pytest.mark.unit
def test_failure_without_a_kind_is_an_error_whatever_its_message(caplog: pytest.LogCaptureFixture) -> None:
    """The level comes from the worker's error_kind, never from words in the message."""
    import logging

    message = "[Errno 13] Permission denied: '/Users/x/Library/Mail'"
    lines, counts, events = _error_result_log(caplog, message=message, error_kind=None)

    assert [level for level, _ in lines] == [logging.ERROR]
    assert counts == {"failed": 1, "access_denied": 0, "files_not_scanned": 0}
    assert events[0][0] == "ERROR"


@pytest.mark.unit
def test_archive_batch_refused_mid_member_is_not_requeued(caplog: pytest.LogCaptureFixture) -> None:
    """Re-queuing it would count the member in progress as an error, then the batch again as access denied."""
    import logging

    from piidigger.models.tasks import Task, TaskResult, TaskType
    from piidigger.orchestration.coordinator import _handle_result
    from piidigger.orchestration.progress import ProgressDisplay
    from piidigger.orchestration.registry import TaskRegistry

    logger = logging.getLogger("tests.coordinator_helpers.denied_batch")
    dispatched: list[Task] = []
    registry = TaskRegistry(dispatched.append)
    task = Task(task_type=TaskType.SCAN_ARCHIVE_MEMBERS, payload={"archive_path": "/x/a.zip"}, items=("a", "b", "c"))
    registry.enqueue(task)
    registry.record_start(task.task_id, 42)
    registry.record_progress(task.task_id, 42, "item_started", "a")
    message = "[Errno 13] Permission denied: '/x/a.zip'"
    result = TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="error",
        error_message=message,
        error_kind="access_denied",
        worker_pid=42,
    )
    progress = ProgressDisplay()

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        _handle_result(result, registry, [], progress, logger)

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage().startswith("[scan_archive_members] path=/x/a.zip: "), "the archive, not the member"
    assert dispatched == [task], "not re-queued"
    assert task.task_id not in registry
    work = progress.incomplete
    assert (work.failed, work.access_denied) == (0, 1)
    assert progress._counters["files_not_scanned"] == 3


def _feed_denied_results(
    caplog: pytest.LogCaptureFixture,
    *,
    notice: FullDiskAccessNotice | None,
    count: int,
    kinds: tuple[str, ...] = ("not_permitted",),
) -> tuple[list[str], list[tuple[str, str]]]:
    """Feed count access-denied ENUM_DIR results to _handle_result: the WARNING lines and the events.

    The nth result has kinds[n], or the last of kinds once they run out.
    """
    import logging

    from piidigger.models.tasks import Task, TaskResult, TaskType
    from piidigger.orchestration.coordinator import _handle_result
    from piidigger.orchestration.progress import ProgressDisplay
    from piidigger.orchestration.registry import TaskRegistry

    logger = logging.getLogger("tests.coordinator_helpers.full_disk_access")
    registry = TaskRegistry(lambda _task: None)
    progress = ProgressDisplay()
    with caplog.at_level(logging.WARNING, logger=logger.name):
        for n in range(count):
            folder = f"/Users/x/Library/Folder{n}"
            task = Task(task_type=TaskType.ENUM_DIR, payload={"path": folder})
            registry.enqueue(task)
            result = TaskResult(
                task_id=task.task_id,
                task_type=task.task_type,
                status="error",
                error_message=f"[Errno 1] Operation not permitted: '{folder}'",
                error_kind=kinds[min(n, len(kinds) - 1)],  # type: ignore[arg-type]
            )
            _handle_result(result, registry, [], progress, logger, notice=notice)
    return [r.getMessage() for r in caplog.records], list(progress._events)


@pytest.mark.unit
def test_full_disk_access_warning_is_given_once_at_the_first_access_denied(caplog: pytest.LogCaptureFixture) -> None:
    """A refusal the startup check did not list still gets the hint during the scan, once."""
    lines, events = _feed_denied_results(caplog, notice=FullDiskAccessNotice(), count=3)

    hints = [line for line in lines if "Full Disk Access" in line]
    assert len(hints) == 1
    assert lines.index(hints[0]) == 1, "right after the first access denied line"
    assert "System Settings › Privacy & Security › Full Disk Access" in hints[0]
    assert "access denied" not in hints[0].lower(), "the summary's search for refused paths must not find it"
    assert sum("Full Disk Access" in text for _, text in events) == 1


@pytest.mark.unit
def test_no_full_disk_access_warning_unless_armed(caplog: pytest.LogCaptureFixture) -> None:
    """Off macOS, or when the startup check already named the folders, run_scan does not arm it."""
    lines, events = _feed_denied_results(caplog, notice=None, count=2)

    assert not any("Full Disk Access" in line for line in lines)
    assert not any("Full Disk Access" in text for _, text in events)


@pytest.mark.unit
def test_full_disk_access_warning_waits_for_operation_not_permitted(caplog: pytest.LogCaptureFixture) -> None:
    """Permission denied is ordinary permissions: Full Disk Access would not help, so it must not spend the notice."""
    lines, _ = _feed_denied_results(
        caplog, notice=FullDiskAccessNotice(), count=3, kinds=("access_denied", "access_denied", "not_permitted")
    )

    hints = [n for n, line in enumerate(lines) if "Full Disk Access" in line]
    assert hints == [3], "given once, after the third refusal, the first not permitted"


@pytest.mark.unit
@pytest.mark.parametrize(("kind", "not_permitted"), [("not_permitted", 1), ("access_denied", 0)])
def test_operation_not_permitted_counts_as_access_denied_and_on_its_own(
    caplog: pytest.LogCaptureFixture, kind: str, not_permitted: int
) -> None:
    """One access-denied row for both; the not permitted count decides the summary's macOS hint."""
    import logging

    from piidigger.models.tasks import Task, TaskResult, TaskType
    from piidigger.orchestration.coordinator import _handle_result
    from piidigger.orchestration.progress import ProgressDisplay
    from piidigger.orchestration.registry import TaskRegistry

    registry = TaskRegistry(lambda _task: None)
    task = Task(task_type=TaskType.ENUM_DIR, payload={"path": "/Users/x/Library/Mail"})
    registry.enqueue(task)
    progress = ProgressDisplay()
    result = TaskResult(
        task_id=task.task_id, task_type=task.task_type, status="error", error_message="refused", error_kind=kind
    )  # type: ignore[arg-type]

    _handle_result(result, registry, [], progress, logging.getLogger("tests.coordinator_helpers.kinds"))

    work = progress.incomplete
    assert (work.failed, work.access_denied, work.not_permitted) == (0, 1, not_permitted)
