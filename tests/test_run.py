"""Unit and integration tests for run.py."""

from __future__ import annotations

import logging
import logging.handlers
import math
import tempfile
from pathlib import Path

import pytest

from piidigger.models.config import Config, ResultsConfig
from piidigger.orchestration.coordinator import CoordinatorResult
from piidigger.orchestration.pool import WorkerPool
from piidigger.outputhandlers import TextSink
from piidigger.outputhandlers.json import JsonSink
from piidigger.run import (
    EXIT_ABORTED,
    EXIT_INCOMPLETE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    _build_sinks,
    _is_broad_folder,
    _output_exclusions,
    _remove_temp_workspace,
    _resolve_workers,
    run_scan,
)
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
def test_run_scan_aborts_when_results_cannot_be_opened(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A scan with nowhere to put its results must not start.

    The failure is reported on stderr and in the log file, not lost to a sink's
    unhandled module logger.
    """
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    blocker = tmp_path / "results"
    blocker.write_text("a file where the results folder should be")
    log_file = tmp_path / "test.log"

    rc = run_scan(
        Config(start_dirs=[scan_root], log_file=log_file, results=ResultsConfig(path=blocker, formats=["text"]))
    )

    assert rc == EXIT_ABORTED
    assert "Error: cannot open results file" in capsys.readouterr().err
    assert "cannot open results file, aborting" in log_file.read_text()
    assert not any(isinstance(h, logging.handlers.QueueHandler) for h in logging.getLogger().handlers)


@pytest.mark.integration
def test_run_scan_reports_incomplete_when_a_sink_fails_mid_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A results file that stops accepting findings (e.g. a full disk) is logged
    once, named in the summary, and turns the exit code into EXIT_INCOMPLETE.
    The other sinks keep receiving findings."""
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    for i in range(3):
        (scan_root / f"card{i}.txt").write_text("card 4111111111111111")

    def disk_full(self: object, record: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("piidigger.outputhandlers.text.TextSink.write", disk_full)
    results_dir = tmp_path / "results"
    log_file = tmp_path / "test.log"

    rc = run_scan(
        Config(
            start_dirs=[scan_root],
            log_file=log_file,
            results=ResultsConfig(path=results_dir, formats=["text", "csv"]),
        )
    )

    assert rc == EXIT_INCOMPLETE
    log_text = log_file.read_text()
    assert log_text.count("write failed, no further results written to it") == 1
    assert "Results incomplete: TextSink" in capsys.readouterr().out
    csv_rows = next(results_dir.glob("*.csv")).read_text().splitlines()
    assert len(csv_rows) == 1 + 3, "the healthy CSV sink still received every finding"


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
    relative default ./piidigger-results/ and ./logs/, so workers tried to read
    files the scan was still writing.
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


@pytest.mark.unit
def test_is_broad_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a folder that holds nothing but output is excluded as a whole."""
    cwd = tmp_path / "cwd"
    (cwd / "logs").mkdir(parents=True)
    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    monkeypatch.chdir(cwd)
    resolved_tmp = tmp_path.resolve()

    assert _is_broad_folder(Path(resolved_tmp.anchor), [scan_root]), "a filesystem root"
    assert _is_broad_folder(cwd.resolve(), [scan_root]), "the cwd"
    assert _is_broad_folder(resolved_tmp, [scan_root]), "an ancestor of the cwd"
    assert _is_broad_folder(scan_root.resolve(), [scan_root / "sub"]), "an ancestor of a start dir"
    assert not _is_broad_folder((cwd / "logs").resolve(), [scan_root]), "a dedicated folder"


@pytest.mark.integration
def test_run_scan_bare_output_paths_exclude_only_the_output_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Output written straight into the cwd must not exclude the cwd.

    Regression test: log_file = "piidigger.log" made the log folder ".", which
    resolved to the cwd, so every subdirectory of it was skipped.
    """
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "nested.txt").write_text("card 4111111111111111")
    (root / "top.txt").write_text("card 4111111111111111")
    monkeypatch.chdir(root)

    config = Config(
        start_dirs=[root],
        log_file=Path("piidigger.log"),
        log_level="DEBUG",
        results=ResultsConfig(path=Path("."), formats=["text"]),
    )
    rc = run_scan(config)

    assert rc == EXIT_OK
    output = next(p for p in root.glob("*.txt") if p.name != "top.txt")
    findings = output.read_text()
    assert "nested.txt" in findings, "subdirectory of the cwd was excluded"
    assert "top.txt" in findings
    assert output.name not in findings, "scan read its own results file"
    assert "piidigger.log" not in findings, "scan read its own log file"


@pytest.mark.unit
def test_output_exclusions_list_the_json_sinks_jsonl_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """JsonSink streams <stem>.jsonl all run; the plaintext handler accepts it as application/json."""
    import os

    monkeypatch.chdir(tmp_path)  # results in the cwd: too broad, so only the files are excluded
    config = Config(
        start_dirs=[tmp_path], log_file=tmp_path / "logs" / "piidigger.log", results=ResultsConfig(path=tmp_path)
    )
    sink = JsonSink(tmp_path / "results.json")

    _, files = _output_exclusions(config, [sink], logging.getLogger("tests.run"))

    assert files == {os.path.normcase(os.path.realpath(p)) for p in sink.paths}


@pytest.mark.integration
def test_run_scan_cleans_up_when_workers_cannot_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A startup failure closes the results files and logs the failure.

    Regression test: pool.start() ran outside the try, so a spawn failure left
    the sinks open and the reason for the failure out of the log file.
    """
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    log_file = tmp_path / "test.log"

    def no_workers(self: object, n: int) -> None:
        raise OSError(1455, "The paging file is too small for this operation to complete")

    closed: list[object] = []
    real_close = TextSink.close

    def recording_close(self: TextSink) -> None:
        closed.append(self)
        real_close(self)

    monkeypatch.setattr("piidigger.run.WorkerPool.start", no_workers)
    monkeypatch.setattr(TextSink, "close", recording_close)

    before = set(Path(tempfile.gettempdir()).glob("piidigger_*"))
    with pytest.raises(OSError, match="paging file"):
        run_scan(
            Config(
                start_dirs=[scan_root],
                log_file=log_file,
                results=ResultsConfig(path=tmp_path / "results", formats=["text"]),
            )
        )

    assert len(closed) == 1, "the results file was left open"
    assert "scan failed during startup" in log_file.read_text()
    assert not set(Path(tempfile.gettempdir()).glob("piidigger_*")) - before
    assert not any(isinstance(h, logging.handlers.QueueHandler) for h in logging.getLogger().handlers)


@pytest.mark.unit
def test_remove_temp_workspace_warns_about_files_it_cannot_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A temp file still locked after the retries is reported, not raised."""
    stuck = tmp_path / "member.txt"
    calls: list[Path] = []

    def still_locked(root: Path) -> list[Path]:
        calls.append(root)
        return [stuck]

    monkeypatch.setattr("piidigger.run.secure_rmtree", still_locked)
    monkeypatch.setattr("piidigger.run._TEMP_REMOVE_RETRY_SECONDS", 0.0)

    _remove_temp_workspace(tmp_path)

    assert len(calls) == 3
    assert f"could not securely remove all temporary files under {tmp_path}" in capsys.readouterr().err


@pytest.mark.unit
def test_remove_temp_workspace_still_warns_when_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second CTRL-C during the retry wait must not swallow the warning.

    Regression test: the interrupt escaped run_scan's finally, skipping the
    warning about plaintext leftovers and stopping the listener.
    """
    monkeypatch.setattr("piidigger.run.secure_rmtree", lambda _root: [tmp_path / "member.txt"])

    def interrupted_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("piidigger.run.time.sleep", interrupted_sleep)

    _remove_temp_workspace(tmp_path)  # must not raise

    assert "could not securely remove all temporary files" in capsys.readouterr().err


def _startup_config(tmp_path: Path) -> Config:
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    return Config(
        start_dirs=[scan_root],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=["text"]),
    )


def _record_sink_closes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    closed: list[object] = []
    real_close = TextSink.close

    def recording_close(self: TextSink) -> None:
        closed.append(self)
        real_close(self)

    monkeypatch.setattr(TextSink, "close", recording_close)
    return closed


@pytest.mark.integration
def test_run_scan_ctrl_c_during_startup_exits_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: KeyboardInterrupt escaped to Click, which exits 1 (EXIT_ABORTED), not 130."""

    def interrupted(self: object, n: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("piidigger.run.WorkerPool.start", interrupted)
    closed = _record_sink_closes(monkeypatch)
    config = _startup_config(tmp_path)

    rc = run_scan(config)

    assert rc == EXIT_INTERRUPTED
    assert len(closed) == 1
    assert "scan interrupted during startup" in config.log_file.read_text()


@pytest.mark.integration
def test_run_scan_cleans_up_when_setup_fails_before_the_pool_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: building the exclusions ran outside the try, so a deleted
    cwd leaked the temp workspace, the open results files and the listener."""

    def cwd_gone(*_args: object) -> None:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr("piidigger.run._output_exclusions", cwd_gone)
    closed = _record_sink_closes(monkeypatch)
    config = _startup_config(tmp_path)

    before = set(Path(tempfile.gettempdir()).glob("piidigger_*"))
    with pytest.raises(FileNotFoundError):
        run_scan(config)

    assert len(closed) == 1
    assert "scan failed during startup" in config.log_file.read_text()
    assert not set(Path(tempfile.gettempdir()).glob("piidigger_*")) - before
    assert not any(isinstance(h, logging.handlers.QueueHandler) for h in logging.getLogger().handlers)


@pytest.mark.integration
def test_run_scan_stops_started_workers_when_startup_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Workers already running when startup fails are stopped before run_scan returns."""
    pools: list[WorkerPool] = []

    class RecordingPool(WorkerPool):
        def start(self, n: int) -> None:
            pools.append(self)
            super().start(n)

    def display_fails(self: object) -> None:
        raise RuntimeError("no terminal")

    monkeypatch.setattr("piidigger.run.WorkerPool", RecordingPool)
    monkeypatch.setattr("piidigger.run.ProgressDisplay.start", display_fails)

    with pytest.raises(RuntimeError, match="no terminal"):
        run_scan(_startup_config(tmp_path))

    assert pools and pools[0].processes, "the pool started workers"
    assert all(not proc.is_alive() for proc in pools[0].processes)
