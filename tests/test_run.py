"""Unit and integration tests for run.py."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import pytest

from piidigger.models.config import Config, ResultsConfig
from piidigger.run import EXIT_OK, _build_sinks, _resolve_workers, run_scan


@pytest.mark.unit
@pytest.mark.parametrize(
    ("physical_cores", "logical_cores"),
    [
        (1, 2),
        (8, 16),
        (12, 20),
    ],
)
def test_resolve_workers_slow_always_returns_one(
    physical_cores: int,
    logical_cores: int,
) -> None:
    assert _resolve_workers("slow", physical_cores, logical_cores) == 1


@pytest.mark.unit
@pytest.mark.parametrize("logical_cores", [0, 1, 8, 16])
def test_resolve_workers_fast_uses_logical_cores(logical_cores: int) -> None:
    assert _resolve_workers("fast", physical_cores=4, logical_cores=logical_cores) == max(1, logical_cores)


@pytest.mark.unit
@pytest.mark.parametrize("physical_cores", [1, 2, 3, 8, 12])
def test_resolve_workers_balanced_uses_physical_core_formula(physical_cores: int) -> None:
    expected = max(1, math.ceil(physical_cores * 0.75))
    assert _resolve_workers("balanced", physical_cores=physical_cores, logical_cores=physical_cores * 2) == expected


@pytest.mark.unit
@pytest.mark.parametrize("logical_cores", [1, 2, 8, 16])
def test_resolve_workers_balanced_falls_back_to_logical_cores(logical_cores: int) -> None:
    expected = max(1, math.ceil(logical_cores * 0.75))
    assert _resolve_workers("balanced", physical_cores=0, logical_cores=logical_cores) == expected


@pytest.mark.unit
def test_resolve_workers_rejects_unknown_preset() -> None:
    with pytest.raises(ValueError, match="unknown performance preset"):
        _resolve_workers("turbo", 8, 16)


# ---------------------------------------------------------------------------
# _build_sinks unit tests (no subprocesses)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_sinks_no_formats_returns_empty(tmp_path: Path) -> None:
    """formats=[] → active is empty → _build_sinks returns [] without creating files."""
    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=[]),
    )
    sinks = _build_sinks(config)
    assert sinks == []


@pytest.mark.unit
def test_build_sinks_all_formats_returns_three_sinks(tmp_path: Path) -> None:
    """formats=["all"] → one sink each for csv, json, text."""
    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=["all"]),
    )
    sinks = _build_sinks(config)
    assert len(sinks) == 3
    type_names = {type(s).__name__ for s in sinks}
    assert type_names == {"CsvSink", "JsonSink", "TextSink"}


@pytest.mark.unit
def test_build_sinks_single_format(tmp_path: Path) -> None:
    """formats=["text"] → exactly one TextSink."""
    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=["text"]),
    )
    sinks = _build_sinks(config)
    assert len(sinks) == 1
    assert type(sinks[0]).__name__ == "TextSink"


# ---------------------------------------------------------------------------
# run_scan integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_run_scan_returns_0_on_success(tmp_path: Path) -> None:
    """run_scan() with a single-file directory returns 0 and creates the output file."""
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    # Include a Luhn-valid PAN so the coordinator exercises _route_to_sinks with findings
    (scan_root / "hello.txt").write_text("hello world 4111111111111111")

    results_dir = tmp_path / "results"
    config = Config(
        start_dirs=[scan_root],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=results_dir, formats=["text"]),
    )

    rc = run_scan(config)

    assert rc == EXIT_OK
    txt_files = list(results_dir.glob("*.txt"))
    assert len(txt_files) == 1, f"expected one .txt output file; got {txt_files}"


@pytest.mark.integration
def test_run_scan_removes_temp_workspace(tmp_path: Path) -> None:
    """The piidigger_* temp root is removed after a successful scan.

    Guards the try/finally around run_coordinator: extracted archive members are
    plaintext PII, so the workspace must not survive the run.
    """
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    (scan_root / "hello.txt").write_text("hello world")

    before = set(Path(tempfile.gettempdir()).glob("piidigger_*"))
    run_scan(
        Config(
            start_dirs=[scan_root],
            log_file=tmp_path / "test.log",
            results=ResultsConfig(path=tmp_path / "results", formats=["text"]),
        )
    )
    leaked = set(Path(tempfile.gettempdir()).glob("piidigger_*")) - before
    assert not leaked, f"temp workspace(s) left behind: {leaked}"


@pytest.mark.integration
def test_run_scan_temp_workspace_removed_when_coordinator_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception escaping run_coordinator still removes the temp workspace.

    Before the try/finally this was the leak path: the scan aborted with a
    traceback and left extracted archive members on disk.
    """
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    (scan_root / "hello.txt").write_text("hello world")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("coordinator exploded")

    monkeypatch.setattr("piidigger.run.run_coordinator", _boom)

    before = set(Path(tempfile.gettempdir()).glob("piidigger_*"))
    with pytest.raises(RuntimeError, match="coordinator exploded"):
        run_scan(
            Config(
                start_dirs=[scan_root],
                log_file=tmp_path / "test.log",
                results=ResultsConfig(path=tmp_path / "results", formats=["text"]),
            )
        )
    leaked = set(Path(tempfile.gettempdir()).glob("piidigger_*")) - before
    assert not leaked, f"temp workspace(s) left behind after a raise: {leaked}"
