"""Unit tests for GuardedSink and the results-files check in the summary."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from piidigger.models.results import ResultRecord
from piidigger.orchestration.progress import ProgressDisplay, ResultsFile
from piidigger.orchestration.sinks import GuardedSink
from piidigger.outputhandlers.csv import CsvSink
from piidigger.outputhandlers.json import JsonSink
from piidigger.outputhandlers.text import TextSink

_LOG = logging.getLogger("tests.sink_guard")


def _progress() -> ProgressDisplay:
    display = ProgressDisplay()
    display._is_tty = False
    return display


def _record() -> ResultRecord:
    return ResultRecord(source_path="/data/a.txt", handler="pan", matches={"visa": ["4111 11** **** 1111"]})


class _FakeSink:
    """Raises each queued write error in turn, then accepts writes."""

    def __init__(self, *, write_errors: list[Exception] | None = None, close_error: OSError | None = None) -> None:
        self.path = Path("/results/out.txt")
        self.paths = (self.path,)
        self.writes = 0
        self.closes = 0
        self.records: list[ResultRecord] = []
        self._write_errors = list(write_errors or [])
        self._close_error = close_error

    def write(self, record: ResultRecord) -> None:
        self.writes += 1
        if self._write_errors:
            raise self._write_errors.pop(0)
        self.records.append(record)

    def close(self) -> None:
        self.closes += 1
        if self._close_error is not None:
            raise self._close_error


def _disk_full() -> OSError:
    return OSError(28, "No space left on device")


# A file name that is not valid UTF-8 arrives on POSIX as surrogate escapes.
_SURROGATE_PATH = "/data/caf\udce9.txt"


@pytest.mark.unit
def test_healthy_sink_passes_writes_through_and_counts_results() -> None:
    sink = _FakeSink()
    guarded = GuardedSink(sink, _LOG, _progress())
    two_values = ResultRecord(source_path="/data/b.txt", handler="email", matches={"email": ["a@x.io", "b@x.io"]})

    guarded.write(_record())
    guarded.write(two_values)
    guarded.close()

    assert (sink.writes, sink.closes) == (2, 1)
    assert guarded.written == 3, "written counts values, as the results-found counter does"
    assert not guarded.failed


@pytest.mark.unit
def test_write_failure_is_logged_once_and_further_writes_skipped(caplog: pytest.LogCaptureFixture) -> None:
    sink = _FakeSink(write_errors=[_disk_full() for _ in range(5)])
    guarded = GuardedSink(sink, _LOG, _progress())

    with caplog.at_level(logging.ERROR, logger=_LOG.name):
        for _ in range(5):
            guarded.write(_record())

    assert sink.writes == 1, "a failed sink must not be written to again, and an OSError is not retried"
    assert guarded.failed
    assert guarded.written == 0
    assert "No space left" in (guarded.error or "")
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


@pytest.mark.unit
def test_value_error_retries_the_record_escaped_and_keeps_the_sink(caplog: pytest.LogCaptureFixture) -> None:
    sink = _FakeSink(write_errors=[ValueError("bad text")])
    guarded = GuardedSink(sink, _LOG, _progress())
    record = ResultRecord(source_path=_SURROGATE_PATH, handler="pan", matches={"visa": ["4111 11** **** 1111"]})

    with caplog.at_level(logging.WARNING, logger=_LOG.name):
        guarded.write(record)
        guarded.write(_record())

    assert not guarded.failed
    assert guarded.written == 2, "the retried record and the next one both count"
    assert [r.source_path for r in sink.records] == ["/data/caf\\udce9.txt", "/data/a.txt"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.unit
def test_value_error_on_the_retry_too_stops_the_sink() -> None:
    sink = _FakeSink(write_errors=[ValueError("bad text"), ValueError("still bad")])
    guarded = GuardedSink(sink, _LOG, _progress())

    guarded.write(_record())
    guarded.write(_record())

    assert guarded.failed
    assert "still bad" in (guarded.error or "")
    assert sink.writes == 2, "one retry, then nothing more"
    assert guarded.written == 0


@pytest.mark.unit
@pytest.mark.parametrize("sink_class", [CsvSink, TextSink])
def test_unencodable_file_name_is_written_escaped(
    tmp_path: Path, sink_class: type, caplog: pytest.LogCaptureFixture
) -> None:
    sink = sink_class(tmp_path / "out")
    sink.open()
    guarded = GuardedSink(sink, _LOG, _progress())
    record = ResultRecord(source_path=_SURROGATE_PATH, handler="pan", matches={"visa": ["4111 11** **** 1111"]})

    with caplog.at_level(logging.WARNING, logger=_LOG.name):
        guarded.write(record)
        guarded.write(_record())
    guarded.close()

    assert not caplog.records, "the sink escapes the text itself, with no retry"
    assert not guarded.failed
    assert guarded.written == 2
    text = (tmp_path / "out").read_bytes().decode("utf-8")  # valid UTF-8 throughout
    assert "/data/caf\\udce9.txt" in text
    assert "/data/a.txt" in text


@pytest.mark.unit
def test_failed_sink_is_still_closed_without_a_second_report(caplog: pytest.LogCaptureFixture) -> None:
    sink = _FakeSink(write_errors=[_disk_full()], close_error=_disk_full())
    guarded = GuardedSink(sink, _LOG, _progress())

    with caplog.at_level(logging.ERROR, logger=_LOG.name):
        guarded.write(_record())
        guarded.close()

    assert sink.closes == 1, "close() releases the file handle even after a failure"
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


@pytest.mark.unit
def test_close_failure_on_healthy_sink_is_reported() -> None:
    guarded = GuardedSink(_FakeSink(close_error=_disk_full()), _LOG, _progress())

    guarded.close()

    assert guarded.failed


@pytest.mark.unit
def test_label_names_sink_type_and_path() -> None:
    guarded = GuardedSink(_FakeSink(), _LOG, _progress())

    assert guarded.label == f"_FakeSink ({Path('/results/out.txt')})"
    assert guarded.path == str(Path("/results/out.txt"))


def _found(progress: ProgressDisplay, count: int) -> ProgressDisplay:
    progress.update({"results_found": count})
    return progress


@pytest.mark.unit
def test_summary_confirms_every_results_file_in_one_line() -> None:
    progress = _found(_progress(), 5)
    progress.report_results_files([ResultsFile("out.csv", 5), ResultsFile("out.json", 5)])

    assert progress.short_results_files == ()
    assert progress.summary_lines(for_log=True)[-1] == (
        logging.INFO,
        "Results files: all 5 results written to every results file.",
    )


@pytest.mark.unit
def test_summary_confirms_a_single_results_file() -> None:
    progress = _found(_progress(), 1)
    progress.report_results_files([ResultsFile("out.csv", 1)])

    assert progress.summary_lines(for_log=True)[-1] == (
        logging.INFO,
        "Results files: all 1 result written to every results file.",
    )


@pytest.mark.unit
def test_summary_has_no_results_files_line_when_nothing_was_found() -> None:
    progress = _progress()
    progress.report_results_files([ResultsFile("out.csv", 0)])

    assert not any("Results files" in text for _, text in progress.summary_lines(for_log=True))


@pytest.mark.unit
def test_summary_lists_each_file_when_one_fell_short() -> None:
    progress = _found(_progress(), 88_441)
    progress.report_results_files(
        [
            ResultsFile("out.csv", 43_735, problem="stopped after an error: [Errno 28] No space left on device"),
            ResultsFile("out.json", 88_441),
        ]
    )

    assert progress.short_results_files == ("out.csv",)
    assert progress.summary_lines(for_log=True)[-3:] == [
        (logging.WARNING, "Results files incomplete (88,441 results found)."),
        (
            logging.WARNING,
            "   43,735  out.csv  (44,706 missing: stopped after an error: [Errno 28] No space left on device)",
        ),
        (logging.WARNING, "   88,441  out.json"),
    ]


@pytest.mark.unit
def test_summary_flags_a_file_missing_results_without_an_error() -> None:
    """A finding the coordinator dropped is in the count but in no file."""
    progress = _found(_progress(), 3)
    progress.report_results_files([ResultsFile("out.csv", 2)])

    assert progress.short_results_files == ("out.csv",)
    assert progress.summary_lines(for_log=True)[-1] == (logging.WARNING, "        2  out.csv  (1 missing)")


@pytest.mark.unit
def test_summary_flags_a_close_failure_with_nothing_missing() -> None:
    progress = _found(_progress(), 3)
    progress.report_results_files([ResultsFile("out.json", 3, problem="stopped after an error: disk full")])

    assert progress.short_results_files == ("out.json",)
    assert progress.summary_lines(for_log=True)[-1] == (
        logging.WARNING,
        "        3  out.json  (stopped after an error: disk full)",
    )


@pytest.mark.unit
def test_summary_on_screen_points_to_the_log(capsys: pytest.CaptureFixture[str]) -> None:
    progress = _found(_progress(), 2)
    progress.report_results_files(
        [ResultsFile("out.txt", 1, problem="stopped after an error: [Errno 28] No space left on device")]
    )

    progress.stop()

    out = capsys.readouterr().out
    assert "Results files incomplete (2 results found). See the log for details." in out
    assert "out.txt  (1 missing: stopped after an error: [Errno 28] No space left on device)" in out


class _FlakyFile:
    """Stands in for the .jsonl stream: raises the queued errors, then takes writes."""

    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        if self._errors:
            raise self._errors.pop(0)
        self.lines.append(text)

    def close(self) -> None:
        pass


@pytest.mark.unit
def test_json_row_names_both_files_it_writes(tmp_path: Path) -> None:
    sink = JsonSink(tmp_path / "out.json")
    guarded = GuardedSink(sink, _LOG, _progress())

    assert guarded.path == f"{tmp_path / 'out.json'}, {tmp_path / 'out.jsonl'}"


@pytest.mark.unit
def test_json_array_matches_the_count_when_the_stream_fails(tmp_path: Path) -> None:
    """A record the .jsonl stream did not take must not reach the .json array either."""
    sink = JsonSink(tmp_path / "out.json")
    sink._file = _FlakyFile(_disk_full())  # type: ignore[assignment]
    guarded = GuardedSink(sink, _LOG, _progress())

    guarded.write(_record())
    guarded.close()

    assert guarded.failed
    assert guarded.written == 0
    assert json.loads((tmp_path / "out.json").read_text()) == []


@pytest.mark.unit
def test_json_retry_keeps_the_record_once(tmp_path: Path) -> None:
    sink = JsonSink(tmp_path / "out.json")
    stream = _FlakyFile(ValueError("bad text"))
    sink._file = stream  # type: ignore[assignment]
    guarded = GuardedSink(sink, _LOG, _progress())

    guarded.write(_record())
    guarded.close()

    assert not guarded.failed
    assert guarded.written == 1
    assert len(stream.lines) == 1
    assert len(json.loads((tmp_path / "out.json").read_text())) == 1, "the retried record is in the array once"


@pytest.mark.unit
def test_results_file_is_complete_only_once_closed() -> None:
    guarded = GuardedSink(_FakeSink(), _LOG, _progress())
    guarded.write(_record())

    assert guarded.results_file() == ResultsFile(
        str(Path("/results/out.txt")), 1, "not saved: the scan was force-quit before the file was closed"
    )
    guarded.close()
    assert guarded.results_file() == ResultsFile(str(Path("/results/out.txt")), 1)


@pytest.mark.unit
def test_failed_results_file_keeps_its_own_error_when_never_closed() -> None:
    guarded = GuardedSink(_FakeSink(write_errors=[_disk_full()]), _LOG, _progress())
    guarded.write(_record())

    assert (guarded.results_file().problem or "").startswith("stopped after an error: [Errno 28] No space left")


@pytest.mark.unit
def test_json_errors_name_the_file_that_failed(tmp_path: Path) -> None:
    """A full disk names no file, and the JSON sink writes two."""
    sink = JsonSink(tmp_path / "out.json")
    sink._file = _FlakyFile(_disk_full())  # type: ignore[assignment]
    guarded = GuardedSink(sink, _LOG, _progress())

    guarded.write(_record())

    assert guarded.error == f"[Errno 28] No space left on device: {str(tmp_path / 'out.jsonl')!r}"
    assert guarded.label == f"JsonSink ({tmp_path / 'out.json'}, {tmp_path / 'out.jsonl'})"


@pytest.mark.unit
def test_json_close_errors_name_the_file_that_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing the .jsonl stream and writing the .json array each name their own file."""

    class _CloseFails(_FlakyFile):
        def close(self) -> None:
            raise _disk_full()

    sink = JsonSink(tmp_path / "out.json")
    sink._file = _CloseFails()  # type: ignore[assignment]
    with pytest.raises(OSError) as stream_error:
        sink.close()
    assert stream_error.value.filename == str(tmp_path / "out.jsonl")

    def full_disk(*args: object, **kwargs: object) -> None:
        raise _disk_full()

    monkeypatch.setattr("piidigger.outputhandlers.json.json.dump", full_disk)
    with pytest.raises(OSError) as array_error:
        JsonSink(tmp_path / "out.json").close()
    assert array_error.value.filename == str(tmp_path / "out.json")


@pytest.mark.unit
def test_json_errors_without_an_errno_keep_their_message(tmp_path: Path) -> None:
    sink = JsonSink(tmp_path / "out.json")
    sink._file = _FlakyFile(OSError("not writable"))  # type: ignore[assignment]
    guarded = GuardedSink(sink, _LOG, _progress())

    guarded.write(_record())

    assert guarded.error == "not writable"


@pytest.mark.unit
def test_close_value_error_is_a_failure_not_a_force_quit() -> None:
    class _CloseRaisesValueError(_FakeSink):
        def close(self) -> None:
            raise ValueError("bad state")

    guarded = GuardedSink(_CloseRaisesValueError(), _LOG, _progress())
    guarded.close()

    assert guarded.results_file().problem == "stopped after an error: bad state"
