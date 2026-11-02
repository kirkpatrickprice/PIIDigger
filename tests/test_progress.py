"""Unit tests for ProgressDisplay — Phase 2."""

from __future__ import annotations

import logging

import pytest

from piidigger.orchestration.progress import (
    IncompleteWork,
    ProgressDisplay,
    ResultsFile,
    _fmt_duration,
    describe_incomplete,
)


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
    assert "Scanned: 7 files, 2.0 KB" in captured.out


@pytest.mark.unit
def test_stop_with_no_counters_prints_no_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stop() prints 'No results found.' when all counters are zero."""
    monkeypatch.setattr("rich.console.Console.is_terminal", property(lambda self: False))
    display = ProgressDisplay()
    display.stop()

    captured = capsys.readouterr()
    assert "No results found." in captured.out


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
    assert out.startswith("Scan complete in ")
    assert "Not fully scanned" not in out


@pytest.mark.unit
def test_incomplete_work_gets_its_own_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial coverage is stated in words, not buried among the totals."""
    display = _non_tty(monkeypatch)
    display.update({"files_scanned": 10, "tasks_failed": 1})
    display.update({"tasks_failed": 1})
    display.report_incomplete(timed_out=1, abandoned=1, unfinished=0, interrupted=False)

    assert [text for _, text in display.summary_lines()][1:] == [
        "  Scanned: 10 files",
        "",
        "Not fully scanned. See the log for details.",
        "        4  files, folders or archive members: 2 errors, 1 timed out, 1 crashed a worker",
    ]


@pytest.mark.unit
def test_interrupted_scan_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted scan must not report itself as complete."""
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=0, abandoned=0, unfinished=12, interrupted=True)

    lines = [text for _, text in display.summary_lines()]

    assert lines[0].startswith("Scan interrupted after ")
    assert "12 unfinished when the scan stopped" in lines[-1]


@pytest.mark.unit
def test_unfinished_work_without_interrupt_says_stopped_early(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=0, abandoned=0, unfinished=3, interrupted=False)
    display.stop()

    assert capsys.readouterr().out.startswith("Scan stopped early after ")


@pytest.mark.unit
def test_incomplete_summary_uses_singular_for_one_item(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=1, abandoned=0, unfinished=0, interrupted=False)

    assert display.summary_lines()[-1] == (
        logging.WARNING,
        "        1  file, folder or archive member: 1 timed out",
    )


@pytest.mark.unit
def test_files_over_the_memory_limit_are_reported_as_not_scanned(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=0, abandoned=0, over_memory=2, unfinished=0, interrupted=False)

    lines = [text for _, text in display.summary_lines()]

    assert lines[0].startswith("Scan complete in ")
    assert lines[-1] == "        2  files, folders or archive members: 2 over the memory limit"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("seconds", "expected"), [(0, "0:00:00"), (59.9, "0:00:59"), (3725, "1:02:05"), (90061, "25:01:01")]
)
def test_fmt_duration(seconds: float, expected: str) -> None:
    assert _fmt_duration(seconds) == expected


@pytest.mark.unit
def test_summary_reports_the_run_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import piidigger.orchestration.progress as progress_mod

    clock = iter([100.0, 100.0 + 3725.0, 999_999.0])
    monkeypatch.setattr(progress_mod.time, "monotonic", lambda: next(clock))
    display = _non_tty(monkeypatch)
    display.update({"files_scanned": 3})

    first = display.summary_lines()
    again = display.summary_lines()

    assert first == [
        (logging.INFO, "Scan complete in 1:02:05. No results found."),
        (logging.INFO, "  Scanned: 3 files"),
    ]
    assert again == first, "the run time is fixed at the first call, so the log and the screen agree"


@pytest.mark.unit
def test_summary_lines_for_the_log_drop_the_pointer_to_the_log(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    display.update({"tasks_failed": 1})
    display.report_incomplete(timed_out=0, abandoned=0, over_memory=0, unfinished=0, interrupted=False)
    display.report_results_files([ResultsFile("results.csv", 0, problem="stopped after an error: disk full")])

    screen = display.summary_lines()
    log = display.summary_lines(for_log=True)

    assert [text for _, text in screen][1:] == [
        "",
        "Not fully scanned. See the log for details.",
        "        1  file, folder or archive member: 1 error",
        "",
        "Results files incomplete (0 results found). See the log for details.",
        "        0  results.csv  (stopped after an error: disk full)",
    ]
    assert log[1:] == [
        (logging.WARNING, "Not fully scanned."),
        (logging.WARNING, "        1  file, folder or archive member: 1 error"),
        (logging.WARNING, "Results files incomplete (0 results found)."),
        (logging.WARNING, "        0  results.csv  (stopped after an error: disk full)"),
    ]


@pytest.mark.unit
def test_summary_heading_is_a_warning_when_the_scan_did_not_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=0, abandoned=0, unfinished=4, interrupted=True)

    level, text = display.summary_lines()[0]

    assert level == logging.WARNING
    assert text.startswith("Scan interrupted after ")
    assert text.endswith(". No results found.")


@pytest.mark.unit
def test_incomplete_summary_is_none_when_everything_finished() -> None:
    assert describe_incomplete(IncompleteWork()) is None


@pytest.mark.unit
def test_failures_are_counted_without_report_incomplete() -> None:
    """Failures arrive with results, so they are visible even if the run ends abnormally."""
    display = ProgressDisplay()
    display.update({"tasks_failed": 1})
    assert display.incomplete == IncompleteWork(failed=1)


@pytest.mark.unit
def test_workers_failing_to_start_is_not_reported_as_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing was really scanned, even though no work was left outstanding."""
    display = _non_tty(monkeypatch)
    display.report_incomplete(timed_out=0, abandoned=2, unfinished=0, interrupted=False, workers_failed=True)

    lines = [text for _, text in display.summary_lines()]

    assert ": workers could not start." in lines[0]
    assert lines[0].startswith("Scan stopped early after ")
    assert "2 crashed a worker" in lines[-1]


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "Error: C:\\data\\[draft] notes.txt — bad",  # backslash before "[" would escape it
        "Error: /home/a/[draft] notes.txt — bad",  # bracketed word would be read as a style tag
        "Access denied: D:\\Users\\x\\",
    ],
)
def test_events_panel_shows_messages_literally(message: str) -> None:
    """Event messages are plain text (usually paths), never markup."""
    from rich.console import Console

    display = ProgressDisplay()
    display.log_event("ERROR", message)

    console = Console(width=200, color_system=None)
    with console.capture() as captured:
        console.print(display._build_events_table())
    assert message in captured.get()


# ---------------------------------------------------------------------------
# The summary block, as a whole
# ---------------------------------------------------------------------------


def _laptop_run(monkeypatch: pytest.MonkeyPatch) -> ProgressDisplay:
    import piidigger.orchestration.progress as progress_mod

    clock = iter([0.0, 2473.0])
    monkeypatch.setattr(progress_mod.time, "monotonic", lambda: next(clock))
    display = _non_tty(monkeypatch)
    display.update(
        {
            "results_found": 108_108,
            "results_by_handler:email": 47_373,
            "results_by_handler:pan": 60_735,
            "dirs_scanned": 28_036,
            "dirs_not_scanned": 12,
            "files_scanned": 108_826,
            "bytes_scanned": 6_871_947_673,
            "tasks_failed": 41,
            "files_unreadable": 562,
            "pdfs_no_text": 14,
            "bytes_not_scanned": 999,
            "archive_members_too_deep": 15_696,
            "archive_members_over_total": 3,
            "archive_members_limited": 2,
            "archive_members_unsafe": 1,
            "archive_members_repeated": 1,
            "archive_members_encrypted": 5,
            "archive_members_nested": 7,
            "archive_members_unsupported": 13_787,
            "files_unsupported_by_type:.doc": 114,
            "files_unsupported_by_type:.rtf": 6,
        }
    )
    display.report_incomplete(timed_out=0, abandoned=0, over_memory=10, unfinished=0, interrupted=False)
    return display


@pytest.mark.unit
def test_summary_block_on_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    assert [text for _, text in _laptop_run(monkeypatch).summary_lines()] == [
        "Scan complete in 0:41:13. Found 108,108 results: 60,735 pan, 47,373 email.",
        "  Scanned: 28,036 folders, 108,826 files, 6.4 GB",
        "",
        "Not fully scanned. See the log for details.",
        "       51  files, folders or archive members: 41 errors, 10 over the memory limit",
        '      562  files not read: text encoding could not be detected (search the log for "text encoding")',
        '       14  PDFs with no extractable text (search the log for "no extractable text")',
        "   15,703  archive members rejected: 15,696 too deep to reach, 3 over the archive's total size, "
        "2 over a member size or count limit, 1 unsafe, 1 repeated",
        "        5  archive members encrypted",
        "        7  nested archives not opened",
        "",
        "Skipped by design:",
        "      120  files of unsupported types",
        "   13,787  archive members of unsupported types",
    ]


@pytest.mark.unit
def test_summary_log_adds_the_types_and_leaves_out_the_screen_formatting(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _laptop_run(monkeypatch)

    screen = display.summary_lines()
    log = display.summary_lines(for_log=True)

    assert log[-1] == (logging.INFO, "Files of unsupported types, by type: .doc ×114, .rtf ×6")
    assert all(text for _, text in log), "no blank lines in the log"
    assert [row for row in screen if row[1] and "See the log" not in row[1]] == [
        row for row in log[:-1] if row[1] != "Not fully scanned."
    ], "the screen and the log must report the same facts"


@pytest.mark.unit
def test_summary_levels_tell_a_problem_from_a_design_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    levels = {text: level for level, text in _laptop_run(monkeypatch).summary_lines()}

    assert (
        levels['      562  files not read: text encoding could not be detected (search the log for "text encoding")']
        == logging.WARNING
    )
    assert levels["   13,787  archive members of unsupported types"] == logging.INFO


@pytest.mark.unit
def test_summary_clean_run_is_two_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    display.update({"files_scanned": 1, "dirs_scanned": 1, "bytes_scanned": 10})
    display.update({"results_found": 1, "results_by_handler:pan": 1})

    lines = [text for _, text in display.summary_lines()]

    assert len(lines) == 2
    assert lines[0].endswith("Found 1 result: 1 pan.")
    assert lines[1] == "  Scanned: 1 folder, 1 file, 10.0 B"


@pytest.mark.unit
def test_summary_new_data_handler_appears_without_any_change(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    display.update({"results_found": 2, "results_by_handler:phone": 2})

    assert display.summary_lines()[0][1].endswith("Found 2 results: 2 phone.")


@pytest.mark.unit
def test_summary_scanned_row_leaves_out_what_was_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bars reach their totals through these counters, but the summary must not call them scanned."""
    display = _non_tty(monkeypatch)
    display.update({"files_scanned": 1, "files_not_scanned": 2, "files_unreadable": 3, "dirs_not_scanned": 4})

    assert "  Scanned: 1 file" in [text for _, text in display.summary_lines()]


@pytest.mark.unit
def test_bars_count_everything_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed or binary file must not leave the files bar short of 100%."""
    display = ProgressDisplay()
    display._is_tty = True
    display._live = None
    monkeypatch.setattr("rich.live.Live.start", lambda self, *a, **k: None)
    display.start()
    display.update(
        {"files_found": 4, "files_scanned": 1, "files_not_scanned": 2, "files_unreadable": 1}
        | {"dirs_found": 2, "dirs_scanned": 1, "dirs_not_scanned": 1}
        | {"bytes_found": 30, "bytes_scanned": 10, "bytes_not_scanned": 20}
    )

    assert display._bars is not None
    assert all(task.percentage == 100 for task in display._bars.tasks)


@pytest.mark.unit
def test_summary_names_the_log_on_screen_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr("rich.console.Console.is_terminal", property(lambda self: False))
    display = ProgressDisplay(log_file=Path("logs/piidigger-20261031-153257.log"))
    display.report_incomplete(timed_out=1, abandoned=0, unfinished=0, interrupted=False)

    screen = [text for _, text in display.summary_lines()]
    log = [text for _, text in display.summary_lines(for_log=True)]

    assert f"Not fully scanned. See the log for details: {Path('logs/piidigger-20261031-153257.log')}" in screen
    assert "Not fully scanned." in log


@pytest.mark.unit
def test_summary_prints_a_path_literally(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from pathlib import Path

    log_file = Path("logs/:warning:/[draft]/piidigger-1.log")
    display = ProgressDisplay(log_file=log_file)
    display._is_tty = True
    display.report_incomplete(timed_out=1, abandoned=0, unfinished=0, interrupted=False)
    display.stop()

    assert str(log_file) in capsys.readouterr().out


@pytest.mark.unit
def test_events_allow_for_a_config_line_that_wraps() -> None:
    from rich.console import Console

    display = ProgressDisplay()
    display._console = Console(width=60, height=30)
    display.set_startup_info(["short"])
    for i in range(25):
        display.log_event("ERROR", f"event {i}")
    with_short_path = len(display._visible_events())

    display.set_startup_info([f"Log file: logs/{'x' * 120}.log"])

    assert display._config_rows() == 4, "the label, then 3 rows of path, at this width"
    assert len(display._visible_events()) == with_short_path - 3


@pytest.mark.unit
def test_config_panel_shows_a_path_literally() -> None:
    from rich.console import Console

    display = ProgressDisplay()
    display.set_startup_info(["Log file: logs/:warning:/piidigger.log"])

    console = Console(width=100, color_system=None)
    with console.capture() as captured:
        console.print(display._build_config_panel())

    assert "logs/:warning:/piidigger.log" in captured.get()


@pytest.mark.unit
def test_summary_end_of_scan_tally_lists_every_document_type_ahead_of_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    noise = {f"files_unsupported_by_type:.n{i:02d}": 1_000 + i for i in range(20)}
    documents = {f"files_unsupported_by_type:{ext}": 5 for ext in (".doc", ".msg", ".ppt", ".pptx", ".rtf")}
    display.update({**noise, **documents})

    line = display.summary_lines(for_log=True)[-1][1]

    tally = line.removeprefix("Files of unsupported types, by type: ")
    assert tally.split(", ")[:5] == [f"{ext} ×5" for ext in (".doc", ".msg", ".ppt", ".pptx", ".rtf")]
    assert tally.count("×") == 20, "all five documents, then the usual 15 others"
    assert tally.endswith("and 5 more types")


@pytest.mark.unit
def test_summary_no_text_row_is_singular_for_one_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _non_tty(monkeypatch)
    display.update({"pdfs_no_text": 1})

    rows = [text for _, text in display.summary_lines()]

    assert '        1  PDF with no extractable text (search the log for "no extractable text")' in rows
