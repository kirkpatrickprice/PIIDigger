import csv
import importlib
import json

import pytest

from piidigger.models.results import ResultRecord
from piidigger.outputhandlers.csv import CsvSink
from piidigger.outputhandlers.json import JsonSink
from piidigger.outputhandlers.text import TextSink
from piidigger.protocols import OutputSink


def _make_record(**kwargs) -> ResultRecord:
    defaults = dict(
        source_path="/tmp/file.txt",
        handler="pan",
        matches={"visa": ["4893 01** **** 6137"]},
    )
    defaults.update(kwargs)
    return ResultRecord(**defaults)


@pytest.mark.unit
class TestCsvSink:
    def test_satisfies_protocol(self, tmp_path):
        assert isinstance(CsvSink(tmp_path / "out.csv"), OutputSink)

    def test_creates_file_with_header(self, tmp_path):
        path = tmp_path / "out.csv"
        sink = CsvSink(path)
        sink.open()
        sink.close()
        assert path.exists()
        with open(path) as f:
            rows = list(csv.DictReader(f))
        assert rows == []

    def test_writes_row_per_value(self, tmp_path):
        path = tmp_path / "out.csv"
        sink = CsvSink(path)
        sink.open()
        sink.write(_make_record(matches={"visa": ["4893 01** **** 6137", "4111 11** **** 1111"]}))
        sink.close()
        with open(path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["handler"] == "pan"
        assert rows[0]["match_type"] == "visa"

    def test_lineage_fields_written(self, tmp_path):
        path = tmp_path / "out.csv"
        record = _make_record(
            source_path="/archive.zip",
            source_member_path="inner/file.txt",
            source_depth=1,
            source_container_type="zip",
        )
        sink = CsvSink(path)
        sink.open()
        sink.write(record)
        sink.close()
        with open(path) as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["source_member_path"] == "inner/file.txt"
        assert rows[0]["source_depth"] == "1"
        assert rows[0]["source_container_type"] == "zip"

    def test_null_lineage_written_as_empty(self, tmp_path):
        path = tmp_path / "out.csv"
        sink = CsvSink(path)
        sink.open()
        sink.write(_make_record())
        sink.close()
        with open(path) as f:
            rows = list(csv.DictReader(f))
        # csv.DictWriter serialises None as "" (empty string); the column is still present
        assert rows[0]["source_member_path"] == ""

    def test_write_before_open_does_not_raise(self, tmp_path):
        sink = CsvSink(tmp_path / "out.csv")
        sink.write(_make_record())  # should silently no-op

    def test_multiple_handlers_in_one_record(self, tmp_path):
        path = tmp_path / "out.csv"
        record = _make_record(matches={"visa": ["V1"], "mc": ["M1"]})
        sink = CsvSink(path)
        sink.open()
        sink.write(record)
        sink.close()
        with open(path) as f:
            rows = list(csv.DictReader(f))
        match_types = {r["match_type"] for r in rows}
        assert match_types == {"visa", "mc"}


@pytest.mark.unit
class TestJsonSink:
    def test_satisfies_protocol(self, tmp_path):
        assert isinstance(JsonSink(tmp_path / "out.json"), OutputSink)

    def test_creates_json_array_on_close(self, tmp_path):
        path = tmp_path / "out.json"
        sink = JsonSink(path)
        sink.open()
        sink.write(_make_record())
        sink.close()
        data = json.loads(path.read_text())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["handler"] == "pan"

    def test_creates_jsonl_during_scan(self, tmp_path):
        path = tmp_path / "out.json"
        sink = JsonSink(path)
        sink.open()
        sink.write(_make_record())
        sink.write(_make_record(source_path="/tmp/other.txt"))
        sink.close()
        jsonl_path = path.with_suffix(".jsonl")
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_empty_scan_writes_empty_array(self, tmp_path):
        path = tmp_path / "out.json"
        sink = JsonSink(path)
        sink.open()
        sink.close()
        data = json.loads(path.read_text())
        assert data == []

    def test_lineage_fields_in_json(self, tmp_path):
        path = tmp_path / "out.json"
        record = _make_record(source_member_path="inner.txt", source_depth=1, source_container_type="zip")
        sink = JsonSink(path)
        sink.open()
        sink.write(record)
        sink.close()
        data = json.loads(path.read_text())
        assert data[0]["source_member_path"] == "inner.txt"
        assert data[0]["source_depth"] == 1
        assert data[0]["source_container_type"] == "zip"


@pytest.mark.unit
class TestTextSink:
    def test_satisfies_protocol(self, tmp_path):
        assert isinstance(TextSink(tmp_path / "out.txt"), OutputSink)

    def test_creates_file(self, tmp_path):
        path = tmp_path / "out.txt"
        sink = TextSink(path)
        sink.open()
        sink.close()
        assert path.exists()

    def test_writes_pipe_separated_line(self, tmp_path):
        path = tmp_path / "out.txt"
        sink = TextSink(path)
        sink.open()
        sink.write(_make_record(source_path="/data/file.txt", handler="pan", matches={"visa": ["4xxx xxxx xxxx 1234"]}))
        sink.close()
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        parts = lines[0].split(" | ")
        assert parts[0] == "/data/file.txt"
        assert parts[1] == "pan"
        assert parts[2] == "visa"
        assert parts[3] == "4xxx xxxx xxxx 1234"

    def test_lineage_appended_when_present(self, tmp_path):
        path = tmp_path / "out.txt"
        record = _make_record(source_member_path="inner.txt", source_depth=1, source_container_type="zip")
        sink = TextSink(path)
        sink.open()
        sink.write(record)
        sink.close()
        line = path.read_text().strip()
        assert "member=inner.txt" in line
        assert "depth=1" in line
        assert "container=zip" in line

    def test_lineage_omitted_when_null(self, tmp_path):
        path = tmp_path / "out.txt"
        sink = TextSink(path)
        sink.open()
        sink.write(_make_record())
        sink.close()
        line = path.read_text().strip()
        assert "member=" not in line
        assert "depth=" not in line
        assert "container=" not in line

    def test_write_before_open_does_not_raise(self, tmp_path):
        sink = TextSink(tmp_path / "out.txt")
        sink.write(_make_record())  # should silently no-op


@pytest.mark.unit
@pytest.mark.parametrize(
    "module",
    [
        "piidigger.outputhandlers.csv",
        "piidigger.outputhandlers.json",
        "piidigger.outputhandlers.text",
        "piidigger.filehandlers.pdf",
    ],
)
def test_handler_modules_do_not_use_logging(module):
    """CLAUDE.md: handlers must not import loggers.  A module logger has no
    handler in the coordinator, so its records went to stderr, not the log."""
    assert not hasattr(importlib.import_module(module), "logging")


@pytest.mark.unit
@pytest.mark.parametrize("sink_cls", [CsvSink, JsonSink, TextSink])
def test_open_failure_raises(tmp_path, sink_cls):
    sink = sink_cls(tmp_path / "missing-dir" / "out.file")

    with pytest.raises(OSError):
        sink.open()


@pytest.mark.unit
@pytest.mark.parametrize("sink_cls", [CsvSink, JsonSink, TextSink])
def test_sinks_expose_path(tmp_path, sink_cls):
    path = tmp_path / "out.file"
    assert sink_cls(path).path == path


class _FailingCloseFile:
    def write(self, data):
        return len(data)

    def close(self):
        raise OSError(28, "No space left on device")


@pytest.mark.unit
def test_json_close_writes_array_even_when_stream_close_fails(tmp_path):
    path = tmp_path / "out.json"
    sink = JsonSink(path)
    sink.open()
    sink._file.close()
    sink._file = _FailingCloseFile()
    sink.write(_make_record())

    with pytest.raises(OSError, match="No space left"):
        sink.close()

    assert len(json.loads(path.read_text())) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "sink_cls, extra_suffixes",
    [(CsvSink, []), (JsonSink, [".jsonl"]), (TextSink, [])],
)
def test_sinks_list_every_file_they_write(tmp_path, sink_cls, extra_suffixes):
    """run_scan excludes these from the scan, so a missing one gets read back as a finding."""
    path = tmp_path / "out.file"
    sink = sink_cls(path)
    sink.open()
    sink.write(_make_record())
    sink.close()

    assert sink.paths == (path, *(path.with_suffix(s) for s in extra_suffixes))
    assert sorted(tmp_path.iterdir()) == sorted(sink.paths)
