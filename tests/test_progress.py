"""Unit tests for ProgressDisplay — Phase 2."""

from __future__ import annotations

import pytest

from piidigger.orchestration.progress import IncompleteWork, ProgressDisplay, _incomplete_summary


@pytest.mark.unit
def test_update_accumulates_counters() -> None:
    """update() sums counter values across multiple calls."""
    display = ProgressDisplay()
    display.update({"files_scanned": 5, "bytes_scanned": 1024})
    display.update({"files_scanned": 3, "bytes_scanned": 512})
    assert display._counters["files_scanned"] == 8
    assert display._counters["bytes_scanned"] == 1536


@pytest.mark.unit
def test_update_handles_unknown_counter_keys() -> None:
    """update() accepts arbitrary counter keys without raising."""
    display = ProgressDisplay()
    display.update({"some_new_counter": 42})
    assert display._counters.get("some_new_counter") == 42


@pytest.mark.unit
def test_noop_in_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """start/update/log_event are silent no-ops when not connected to a TTY."""
    monkeypatch.setattr("rich.console.Console.is_terminal", property(lambda self: False))
    display = ProgressDisplay()
    assert not display._is_tty

    # None of these should raise or produce output
    display.start()
    display.update({"files_scanned": 1})
    display.log_event("WARNING", "test event")

    # Rich objects must remain uninitialised (start() was a no-op)
    assert display._bars is None
    assert display._live is None


@pytest.mark.unit
def test_stop_prints_summary_in_non_tty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stop() prints a plain-text summary line regardless of TTY state."""
    monkeypatch.setattr("rich.console.Console.is_terminal", property(lambda self: False))
    display = ProgressDisplay()
    display.update({"files_scanned": 7, "bytes_scanned": 2048})
    display.stop()

    captured = capsys.readouterr()
    assert "Scan complete" in captured.out
    assert "files_scanned=7" in captured.out
    assert "bytes_scanned=2.0 KB" in captured.out


@pytest.mark.unit
def test_stop_with_no_counters_prints_no_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stop() prints 'No results.' when all counters are zero."""
    monkeypatch.setattr("rich.console.Console.is_terminal", property(lambda self: False))
    display = ProgressDisplay()
    display.stop()

    captured = capsys.readouterr()
    assert "No results." in captured.out


@pytest.mark.unit
def test_log_event_appended_to_internal_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_event() records events in the internal deque regardless of TTY."""
    monkeypatch.setattr("rich.console.Console.is_terminal", property(lambda self: False))
    display = ProgressDisplay()
    display.log_event("WARNING", "disk full")
    display.log_event("ERROR", "permission denied")

    assert len(display._events) == 2
    assert display._events[0] == ("WARNING", "disk full")
    assert display._events[1] == ("ERROR", "permission denied")


# ---------------------------------------------------------------------------
# Incomplete work in the end-of-scan summary
# ---------------------------------------------------------------------------


def _non_tty(monkeypatch: pytest.MonkeyPatch) -> ProgressDisplay:
    monkeypatch.setattr("rich.console.Console.is_terminal", property(lambda self: False))
    return ProgressDisplay()


@pytest.mark.unit
def test_complete_scan_prints_no_incomplete_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    display = _non_tty(monkeypatch)
    display.update({"files_scanned": 3})
    display.report_incomplete(timed_out=0, abandoned=0, unfinished=0, interrupted=False)
    display.stop()

    out = capsys.readouterr().out
    assert out.startswith("Scan complete.")
    assert "Not fully scanned" not in out


@pytest.mark.unit
def test_incomplete_work_gets_its_own_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Partial coverage is stated in words, not buried among the key=value totals."""
    display = _non_tty(monkeypatch)
    display.update({"files_scanned": 10, "tasks_failed": 1})
    display.update({"tasks_failed": 1})
    display.report_incomplete(timed_out=1, abandoned=1, unfinished=0, interrupted=False)
    display.stop()

    first, second = capsys.readouterr().out.splitlines()
    assert first.startswith("Scan complete.")
    assert "tasks_failed" not in first, "failures belong on the second line, not in the totals"
    assert second == (
        "Not fully scanned: 4 files or folders were skipped — 2 failed with an error, "
        "1 timed out, 1 abandoned after repeated worker crashes. See the log for details."
    )


@pytest.mark.unit
def test_interrupted_scan_says_so(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An interrupted scan must not report itself as complete."""
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=0, abandoned=0, unfinished=12, interrupted=True)
    display.stop()

    first, second = capsys.readouterr().out.splitlines()
    assert first.startswith("Scan interrupted.")
    assert "12 unfinished when the scan stopped" in second


@pytest.mark.unit
def test_unfinished_work_without_interrupt_says_stopped_early(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=0, abandoned=0, unfinished=3, interrupted=False)
    display.stop()

    assert capsys.readouterr().out.startswith("Scan stopped early.")


@pytest.mark.unit
def test_incomplete_summary_uses_singular_for_one_item() -> None:
    assert _incomplete_summary(IncompleteWork(timed_out=1)) == (
        "Not fully scanned: 1 file or folder was skipped — 1 timed out. See the log for details."
    )


@pytest.mark.unit
def test_incomplete_summary_is_none_when_everything_finished() -> None:
    assert _incomplete_summary(IncompleteWork()) is None


@pytest.mark.unit
def test_failures_are_counted_without_report_incomplete() -> None:
    """Failures arrive with results, so they are visible even if the run ends abnormally."""
    display = ProgressDisplay()
    display.update({"tasks_failed": 1})
    assert display.incomplete == IncompleteWork(failed=1)
