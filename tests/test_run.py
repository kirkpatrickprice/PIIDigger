"""Unit and integration tests for run.py."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import pytest

from piidigger.models.config import Config, ResultsConfig
from piidigger.orchestration.coordinator import CoordinatorResult
from piidigger.run import EXIT_INCOMPLETE, EXIT_INTERRUPTED, EXIT_OK, _build_sinks, _resolve_workers, run_scan
from tests._fs import make_dir_alias


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
def test_run_scan_excludes_temp_workspace_reached_via_symlink_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp workspace must be excluded even when reached by a route that
    never crosses the symlink alias tempfile.mkdtemp() happened to return it
    through (e.g. macOS's /var -> /private/var).

    Regression test: temp_base must be resolved before being added to
    exclude_dirs.  _is_excluded() always compares against entry.resolve(), so
    an unresolved alias path never matches an entry reached directly through
    the real (already-resolved) route, and a worker ends up scanning its own
    extracted archive members.
    """
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    make_dir_alias(alias, physical)

    temp_dir_name = "piidigger_test"
    (physical / temp_dir_name).mkdir()
    (physical / temp_dir_name / "leftover.txt").write_text("hello world 4111111111111111")

    aliased_mkdtemp_path = str(alias / temp_dir_name)
    monkeypatch.setattr("piidigger.run.tempfile.mkdtemp", lambda prefix="": aliased_mkdtemp_path)

    results_dir = tmp_path / "results"
    config = Config(
        start_dirs=[physical],  # reached directly, never crossing the alias symlink
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=results_dir, formats=["text"]),
    )
    rc = run_scan(config)

    assert rc == EXIT_OK
    txt_files = list(results_dir.glob("*.txt"))
    assert txt_files, "expected an output file"
    findings = txt_files[0].read_text()
    assert "leftover.txt" not in findings, "worker scanned its own extracted-archive temp workspace"


@pytest.mark.integration
def test_run_scan_honours_user_exclude_written_through_symlink_alias(tmp_path: Path) -> None:
    """A configured exclude_dirs entry written through a symlink alias must apply.

    Regression test: on macOS the /etc default resolves to /private/etc, so a
    scan reaching /private/etc never matched the unresolved pattern.
    """
    physical = tmp_path / "physical"
    (physical / "secret").mkdir(parents=True)
    (physical / "secret" / "hidden.txt").write_text("card 4111111111111111")
    (physical / "visible.txt").write_text("card 4111111111111111")
    alias = tmp_path / "alias"
    make_dir_alias(alias, physical)

    results_dir = tmp_path / "results"
    config = Config(
        start_dirs=[physical],  # reached directly, never crossing the alias
        exclude_dirs=[str(alias / "secret")],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=results_dir, formats=["text"]),
    )
    rc = run_scan(config)

    assert rc == EXIT_OK
    findings = next(results_dir.glob("*.txt")).read_text()
    assert "visible.txt" in findings
    assert "hidden.txt" not in findings, "exclude pattern written through an alias was ignored"


@pytest.mark.integration
def test_run_scan_skips_its_own_results_and_log_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan must not read its own output folders.

    Regression test: scanning / or C:\\ from a cwd under it reached the
    relative default ./piidigger-results/ and ./logs/, so every finding was
    reported again against the output files and each later run grew.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.txt").write_text("card 4111111111111111")
    monkeypatch.chdir(root)

    config = Config(start_dirs=[root], results=ResultsConfig(formats=["text"]))  # relative default paths
    (root / config.results.path).mkdir()
    (root / config.results.path / "previous-run.json").write_text('{"match": "4111111111111111"}')
    (root / config.log_file.parent).mkdir()
    (root / config.log_file.parent / "older.log").write_text("found 4111111111111111")

    rc = run_scan(config)

    assert rc == EXIT_OK
    output = next((root / config.results.path).glob("*.txt"))
    findings = output.read_text()
    assert "source.txt" in findings
    assert "previous-run.json" not in findings, "scan read its own results folder"
    assert "older.log" not in findings, "scan read its own log folder"


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


@pytest.mark.integration
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (CoordinatorResult(), EXIT_OK),
        (CoordinatorResult(unfinished=4), EXIT_INCOMPLETE),
        (CoordinatorResult(workers_failed=True), EXIT_INCOMPLETE),
        (CoordinatorResult(interrupted=True, unfinished=4), EXIT_INTERRUPTED),
    ],
)
def test_run_scan_maps_the_coordinator_outcome_to_an_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: CoordinatorResult, expected: int
) -> None:
    """A run whose workers could not start must not exit 0."""
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    monkeypatch.setattr("piidigger.run.run_coordinator", lambda *_args, **_kwargs: outcome)

    rc = run_scan(
        Config(
            start_dirs=[scan_root],
            log_file=tmp_path / "test.log",
            results=ResultsConfig(path=tmp_path / "results", formats=["text"]),
        )
    )

    assert rc == expected
