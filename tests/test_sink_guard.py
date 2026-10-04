"""Unit tests for GuardedSink and the results-failure summary line."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from piidigger.models.results import ResultRecord
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.sinks import GuardedSink

_LOG = logging.getLogger("tests.sink_guard")


def _progress() -> ProgressDisplay:
    display = ProgressDisplay()
    display._is_tty = False
    return display


def _record() -> ResultRecord:
    return ResultRecord(source_path="/data/a.txt", handler="pan", matches={"visa": ["4111 11** **** 1111"]})


class _FakeSink:
    def __init__(self, *, write_error: OSError | None = None, close_error: OSError | None = None) -> None:
        self.path = Path("/results/out.txt")
        self.writes = 0
        self.closes = 0
        self._write_error = write_error
        self._close_error = close_error

    def write(self, record: ResultRecord) -> None:
        self.writes += 1
        if self._write_error is not None:
            raise self._write_error

    def close(self) -> None:
        self.closes += 1
        if self._close_error is not None:
            raise self._close_error


_DISK_FULL = OSError(28, "No space left on device")


@pytest.mark.unit
def test_healthy_sink_passes_writes_through() -> None:
    sink = _FakeSink()
    guarded = GuardedSink(sink, _LOG, _progress())

    guarded.write(_record())
    guarded.write(_record())
    guarded.close()

    assert (sink.writes, sink.closes) == (2, 1)
    assert not guarded.failed


@pytest.mark.unit
def test_write_failure_is_logged_once_and_further_writes_skipped(caplog: pytest.LogCaptureFixture) -> None:
    sink = _FakeSink(write_error=_DISK_FULL)
    progress = _progress()
    guarded = GuardedSink(sink, _LOG, progress)

    with caplog.at_level(logging.ERROR, logger=_LOG.name):
        for _ in range(5):
            guarded.write(_record())

    assert sink.writes == 1, "a failed sink must not be written to again"
    assert guarded.failed
    assert "No space left" in (guarded.error or "")
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    assert progress._output_failures == [(guarded.label, guarded.error)]


@pytest.mark.unit
def test_failed_sink_is_still_closed_without_a_second_report() -> None:
    sink = _FakeSink(write_error=_DISK_FULL, close_error=_DISK_FULL)
    progress = _progress()
    guarded = GuardedSink(sink, _LOG, progress)

    guarded.write(_record())
    guarded.close()

    assert sink.closes == 1, "close() releases the file handle even after a failure"
    assert len(progress._output_failures) == 1


@pytest.mark.unit
def test_close_failure_on_healthy_sink_is_reported() -> None:
    sink = _FakeSink(close_error=_DISK_FULL)
    progress = _progress()
    guarded = GuardedSink(sink, _LOG, progress)

    guarded.close()

    assert guarded.failed
    assert len(progress._output_failures) == 1


@pytest.mark.unit
def test_label_names_sink_type_and_path() -> None:
    assert GuardedSink(_FakeSink(), _LOG, _progress()).label == f"_FakeSink ({Path('/results/out.txt')})"


@pytest.mark.unit
def test_summary_names_each_failed_results_file(capsys: pytest.CaptureFixture[str]) -> None:
    progress = _progress()
    progress.report_output_failure("TextSink (out.txt)", "[Errno 28] No space left on device")

    progress.stop()

    out = capsys.readouterr().out
    assert "Results incomplete: TextSink (out.txt) stopped after an error ([Errno 28] No space left on device)" in out
