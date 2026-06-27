"""Unit tests for ProgressDisplay — Phase 2."""

from __future__ import annotations

import pytest

from piidigger.orchestration.progress import ProgressDisplay


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
    assert "bytes_scanned=2,048" in captured.out


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
