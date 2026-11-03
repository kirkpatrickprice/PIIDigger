"""Unit and integration tests for run.py."""

from __future__ import annotations

import json
import logging
import logging.handlers
import math
import os
import re
import tempfile
import threading
from pathlib import Path

import pytest

import piidigger.run as run_mod
from piidigger.models.config import Config, ResultsConfig
from piidigger.orchestration.coordinator import CoordinatorResult
from piidigger.orchestration.pool import WorkerPool
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.sleep import SleepStatus
from piidigger.outputhandlers import TextSink
from piidigger.outputhandlers.json import JsonSink
from piidigger.run import (
    EXIT_ABORTED,
    EXIT_INCOMPLETE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    _build_sinks,
    _check_memory,
    _describe_platform,
    _emit_startup_info,
    _is_broad_folder,
    _memory_budget,
    _output_exclusions,
    _prompt_continue,
    _remove_temp_workspace,
    _resolve_workers,
    _stamped_log_file,
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


def _written_log(log_file: Path) -> Path:
    """The one log file a run wrote for this configured log_file, whose name it stamps."""
    written = list(log_file.parent.glob(f"{log_file.stem}-*{log_file.suffix}"))
    assert len(written) == 1, written
    return written[0]


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
    sinks = _build_sinks(config, "20260101-000000")
    assert sinks == []


@pytest.mark.unit
def test_build_sinks_all_formats_returns_three_sinks(tmp_path: Path) -> None:
    """formats=["all"] → one sink each for csv, json, text."""
    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=["all"]),
    )
    sinks = _build_sinks(config, "20260101-000000")
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
    sinks = _build_sinks(config, "20260101-000000")
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
def test_run_scan_log_names_every_results_file_and_ends_with_the_exit_code(tmp_path: Path) -> None:
    """A log alone must say where its results went and how the run ended."""
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    (scan_root / "hello.txt").write_text("hello world 4111111111111111")
    results_dir = tmp_path / "results"
    log_file = tmp_path / "test.log"

    rc = run_scan(
        Config(start_dirs=[scan_root], log_file=log_file, results=ResultsConfig(path=results_dir, formats=["all"]))
    )

    assert rc == EXIT_OK
    lines = _written_log(log_file).read_text(encoding="utf-8").splitlines()
    logged = {line.split("Results file: ", 1)[1] for line in lines if " run INFO Results file: " in line}
    assert logged == {str(p) for p in results_dir.iterdir()}, "one line per file: .csv, .json, .jsonl, .txt"
    assert lines[-1].endswith(f"run INFO Exit code: {EXIT_OK}")


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
    log_text = _written_log(log_file).read_text()
    assert "cannot open results file, aborting" in log_text
    assert log_text.splitlines()[-1].endswith(f"Exit code: {EXIT_ABORTED}")
    assert not any(isinstance(h, logging.handlers.QueueHandler) for h in logging.getLogger().handlers)


@pytest.mark.integration
@pytest.mark.skipif(os.name == "nt", reason="NTFS file names are UTF-16, so they cannot hold an invalid UTF-8 byte")
def test_run_scan_writes_findings_from_a_non_utf8_file_name_to_every_results_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file name that is not valid UTF-8 once stopped the CSV and text files for the rest of the run."""
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    try:
        (scan_root / os.fsdecode(b"caf\xe9.txt")).write_text("card 4111111111111111")
    except OSError, UnicodeEncodeError:
        pytest.skip("this file system rejects file names that are not valid UTF-8")
    (scan_root / "plain.txt").write_text("card 5555555555554444")
    results_dir = tmp_path / "results"

    rc = run_scan(
        Config(
            start_dirs=[scan_root],
            log_file=tmp_path / "test.log",
            results=ResultsConfig(path=results_dir, formats=["text", "csv", "json"]),
        )
    )

    assert rc == EXIT_OK
    assert "Results files: all 2 results written to every results file." in capsys.readouterr().out
    for pattern in ("*.csv", "*.txt"):
        text = next(results_dir.glob(pattern)).read_bytes().decode("utf-8")  # valid UTF-8 throughout
        assert "caf\\xe9.txt" in text, "the name's own byte, so it can be found on disk"
        assert "plain.txt" in text
    jsonl = next(results_dir.glob("*.jsonl")).read_bytes().decode("utf-8")
    assert {json.loads(line)["source_path"] for line in jsonl.splitlines()} == {
        str(scan_root / "caf\\xe9.txt"),
        str(scan_root / "plain.txt"),
    }


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
            log_level="WARNING",
            results=ResultsConfig(path=results_dir, formats=["text", "csv"]),
        )
    )

    assert rc == EXIT_INCOMPLETE
    log_text = _written_log(log_file).read_text()
    assert log_text.count("write failed, no further results written to it") == 1
    assert log_text.splitlines()[-1].endswith(f"run WARNING Exit code: {EXIT_INCOMPLETE}"), "kept at WARNING level"
    out = capsys.readouterr().out
    assert "Results files incomplete (3 results found)." in out
    assert "(3 missing: stopped after an error: [Errno 28] No space left on device)" in out
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
    log_text = _written_log(log_file).read_text()
    assert "scan failed during startup" in log_text
    assert log_text.splitlines()[-1].endswith("run WARNING Exit code: 1 (scan failed: OSError)")
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
    assert "scan interrupted during startup" in _written_log(config.log_file).read_text()


@pytest.mark.integration
def test_run_scan_ctrl_c_at_the_admin_prompt_exits_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CTRL-C at a prompt before the scan starts once escaped to Click's exit 1, leaving the log undrained."""

    def interrupted(config: Config, logger: logging.Logger) -> bool:
        logger.warning("admin check: queued before the CTRL-C")
        raise KeyboardInterrupt

    monkeypatch.setattr("piidigger.run._check_admin", interrupted)
    config = _startup_config(tmp_path)

    rc = run_scan(config)

    assert rc == EXIT_INTERRUPTED
    lines = _written_log(config.log_file).read_text().splitlines()
    assert lines[-2].endswith("admin check: queued before the CTRL-C"), "the listener was drained"
    assert lines[-1].endswith(f"run WARNING Exit code: {EXIT_INTERRUPTED}")
    assert not any(isinstance(h, logging.handlers.QueueHandler) for h in logging.getLogger().handlers)


@pytest.mark.integration
def test_run_scan_logs_the_exit_code_when_a_second_ctrl_c_escapes_startup_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_workers(self: object, n: int) -> None:
        raise OSError(1455, "The paging file is too small for this operation to complete")

    def second_ctrl_c(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("piidigger.run.WorkerPool.start", no_workers)
    monkeypatch.setattr("piidigger.run._abort_startup", second_ctrl_c)
    config = _startup_config(tmp_path)

    with pytest.raises(KeyboardInterrupt):
        run_scan(config)

    last = _written_log(config.log_file).read_text().splitlines()[-1]
    assert last.endswith("run WARNING Exit code: 1 (scan failed: OSError)"), "named by the failure that started it"


@pytest.mark.integration
def test_run_scan_called_while_its_caller_handles_an_exception_logs_one_exit_code(tmp_path: Path) -> None:
    """The caller's exception is not this scan's failure."""
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    log_file = tmp_path / "test.log"
    config = Config(start_dirs=[scan_root], log_file=log_file, results=ResultsConfig(path=tmp_path / "r"))

    try:
        raise ValueError("the caller's own")
    except ValueError:
        rc = run_scan(config)

    assert rc == EXIT_OK
    log_text = _written_log(log_file).read_text()
    assert "scan failed" not in log_text
    assert log_text.count("Exit code:") == 1
    assert log_text.splitlines()[-1].endswith(f"run INFO Exit code: {EXIT_OK}")


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
    assert "scan failed during startup" in _written_log(config.log_file).read_text()
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


@pytest.mark.unit
def test_startup_panel_shows_windows_drive_roots_intact() -> None:
    """A start dir ending in a backslash must not escape the closing [/cyan] tag."""
    from rich.console import Console

    progress = ProgressDisplay()
    config = Config(start_dirs=["C:\\", "D:\\"])

    _emit_startup_info(
        progress,
        logging.getLogger("tests.startup"),
        config,
        4,
        SleepStatus.ACTIVE,
        "1,024 MB per worker",
        None,
        results_files=[],
    )

    console = Console(width=200, color_system=None)
    with console.capture() as captured:
        console.print(progress._build_config_panel())
    assert "Scan directories: C:\\, D:\\ " in captured.get()
    assert "[/cyan]" not in captured.get()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "text", "colour"),
    [
        (SleepStatus.ACTIVE, "Sleep prevention: Active", "green"),
        (SleepStatus.ACTIVE_LOGIND, "Sleep prevention: Active (logind)", "green"),
        (SleepStatus.UNAVAILABLE, "Sleep prevention: Unavailable", "yellow"),
        (SleepStatus.UNAVAILABLE_LINUX, "Sleep prevention: Unavailable (no desktop session or logind lock)", "yellow"),
    ],
)
def test_startup_info_reports_sleep_status(
    status: SleepStatus, text: str, colour: str, caplog: pytest.LogCaptureFixture
) -> None:
    from rich.console import Console

    progress = ProgressDisplay()
    logger = logging.getLogger("tests.startup")

    with caplog.at_level(logging.INFO, logger=logger.name):
        _emit_startup_info(
            progress, logger, Config(start_dirs=[]), 1, status, "1,024 MB per worker", None, results_files=[]
        )

    assert text in caplog.messages
    assert f"[{colour}]{status}[/{colour}]" in progress._startup_lines[-2]
    console = Console(width=200, color_system=None)
    with console.capture() as captured:
        console.print(progress._build_config_panel())
    assert text in captured.get()


@pytest.mark.unit
def test_startup_log_names_the_platform(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tests.startup")

    with caplog.at_level(logging.INFO, logger=logger.name):
        _emit_startup_info(
            ProgressDisplay(),
            logger,
            Config(start_dirs=[]),
            1,
            SleepStatus.ACTIVE,
            "1,024 MB per worker",
            None,
            results_files=[],
        )

    assert f"Platform: {_describe_platform()}" in caplog.messages


@pytest.mark.unit
def test_describe_platform_adds_the_linux_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(run_mod.platform, "release", lambda: "7.0.0-generic")
    monkeypatch.setattr(run_mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(run_mod.platform, "freedesktop_os_release", lambda: {"PRETTY_NAME": "Ubuntu 24.04 LTS"})

    assert _describe_platform() == "Ubuntu 24.04 LTS (Linux 7.0.0-generic, x86_64)"


@pytest.mark.unit
@pytest.mark.parametrize("error", [OSError("no os-release file"), KeyError("PRETTY_NAME")])
def test_describe_platform_without_a_distribution_name(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def no_release() -> dict[str, str]:
        raise error

    monkeypatch.setattr(run_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(run_mod.platform, "release", lambda: "11")
    monkeypatch.setattr(run_mod.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(run_mod.platform, "freedesktop_os_release", no_release)

    assert _describe_platform() == "Windows 11, AMD64"


# ---------------------------------------------------------------------------
# Memory limit at startup
# ---------------------------------------------------------------------------

_GB = 1024**3


@pytest.mark.unit
def test_memory_budget_states_the_total_ceiling() -> None:
    description, warning = _memory_budget(1024, 3, 32 * _GB)

    assert description == "1,024 MB per worker (up to ~3.0 GB for 3 workers)"
    assert warning is None


@pytest.mark.unit
def test_memory_budget_warns_when_workers_could_use_over_half_the_ram() -> None:
    description, warning = _memory_budget(1024, 3, 4 * _GB)

    assert description == "1,024 MB per worker (up to ~3.0 GB for 3 workers)"
    assert warning == (
        "PIIDigger may use up to 3.0 GB of this machine's 4.0 GB RAM; "
        'consider performance = "slow" or a lower max_worker_memory_mb'
    )


@pytest.mark.unit
def test_memory_budget_at_exactly_half_the_ram_does_not_warn() -> None:
    assert _memory_budget(1024, 2, 4 * _GB)[1] is None


_WARNING = _memory_budget(1024, 3, 2 * _GB)[1]


@pytest.mark.unit
def test_startup_info_logs_the_memory_limit_and_shows_its_warning(caplog: pytest.LogCaptureFixture) -> None:
    """The warning is logged once, by _check_memory(); the display still shows it."""
    description, warning = _memory_budget(1024, 3, 2 * _GB)
    logger = logging.getLogger("tests.startup.memory")
    progress = ProgressDisplay()

    with caplog.at_level(logging.INFO, logger=logger.name):
        _emit_startup_info(
            progress, logger, Config(start_dirs=[]), 3, SleepStatus.ACTIVE, description, warning, results_files=[]
        )

    messages = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert (logging.INFO, "Memory limit: 1,024 MB per worker (up to ~3.0 GB for 3 workers)") in messages
    assert not any(level == logging.WARNING for level, _ in messages)
    assert any(level == "WARNING" and "of this machine's 2.0 GB RAM" in msg for level, msg in progress._events)


@pytest.mark.unit
def test_memory_check_passes_without_asking_when_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(run_mod, "_prompt_continue", lambda question: pytest.fail("must not prompt"))

    assert _check_memory(None, logging.getLogger("tests.memcheck")) is True


@pytest.mark.unit
@pytest.mark.parametrize("answer", [True, False])
def test_memory_check_prompts_on_a_terminal_and_follows_the_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, answer: bool
) -> None:

    monkeypatch.setattr(run_mod.sys.stdin, "isatty", lambda: True, raising=False)
    questions: list[str] = []
    monkeypatch.setattr(run_mod, "_prompt_continue", lambda question: questions.append(question) or answer)
    logger = logging.getLogger("tests.memcheck")

    with caplog.at_level(logging.INFO, logger=logger.name):
        assert _check_memory(_WARNING, logger) is answer

    assert len(questions) == 1
    assert "of this machine's 2.0 GB RAM" in questions[0]
    messages = [r.getMessage() for r in caplog.records]
    assert any("memory check: PIIDigger may use up to 3.0 GB" in m for m in messages)
    assert ("memory check: scan cancelled by the user" in messages) is not answer


@pytest.mark.unit
def test_memory_check_continues_without_asking_when_not_interactive(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:

    monkeypatch.setattr(run_mod.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(run_mod, "_prompt_continue", lambda question: pytest.fail("must not prompt"))
    logger = logging.getLogger("tests.memcheck")

    with caplog.at_level(logging.INFO, logger=logger.name):
        assert _check_memory(_WARNING, logger) is True

    assert any("non-interactive mode" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_prompt_continue_defaults_to_continue_after_the_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    class _NeverAnswers(io.StringIO):
        def readline(self, size: int | None = -1) -> str:
            threading.Event().wait()  # blocks the daemon reader thread forever
            return ""

    monkeypatch.setattr(run_mod.sys, "stdin", _NeverAnswers())

    assert _prompt_continue("Memory is tight.", timeout=0) is True
    out = capsys.readouterr().out
    assert "Memory is tight.  Continue (Y/n) [0s]: " in out
    assert "continuing scan" in out


@pytest.mark.unit
def test_an_answer_after_a_timed_out_prompt_reaches_the_next_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin prompt timing out must not leave a reader behind that swallows the memory prompt's "n"."""
    import io

    class _LateStdin(io.StringIO):
        """Like a terminal: each typed line is read once, and only after the user types it."""

        def __init__(self) -> None:
            super().__init__()
            self.typed = threading.Event()
            self._lines = ["n\n"]
            self._lock = threading.Lock()

        def readline(self, size: int | None = -1) -> str:
            self.typed.wait()
            with self._lock:
                if self._lines:
                    return self._lines.pop(0)
            threading.Event().wait()
            return ""

    stdin = _LateStdin()
    monkeypatch.setattr(run_mod.sys, "stdin", stdin)

    assert _prompt_continue("Admin user not detected.", timeout=0) is True
    threading.Timer(0.2, stdin.typed.set).start()
    assert _prompt_continue("Memory is tight.", timeout=5) is False


@pytest.mark.unit
def test_prompt_continue_treats_closed_stdin_as_continue_for_every_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    monkeypatch.setattr(run_mod.sys, "stdin", io.StringIO(""))

    assert _prompt_continue("first", timeout=5) is True
    assert _prompt_continue("second", timeout=5) is True, "end of input is not used up by the first prompt"


@pytest.mark.unit
def test_run_scan_aborts_when_the_memory_check_is_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(run_mod, "_check_memory", lambda warning, logger: False)
    config = _startup_config(tmp_path)

    assert run_scan(config) == EXIT_ABORTED
    assert not config.results.path.exists(), "no results files are opened for a declined scan"


@pytest.mark.unit
def test_stamped_log_file_keeps_its_folder_and_suffix() -> None:
    assert _stamped_log_file(Path("logs/piidigger.log"), "20261031-153257") == Path(
        "logs/piidigger-20261031-153257.log"
    )


def test_run_scan_log_and_results_share_one_timestamp(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    results = tmp_path / "results"
    log_file = tmp_path / "logs" / "piidigger.log"

    run_scan(Config(start_dirs=[scan_root], log_file=log_file, results=ResultsConfig(path=results, formats=["csv"])))

    log_stamp = _written_log(log_file).stem.removeprefix("piidigger-")
    assert re.fullmatch(r"\d{8}-\d{6}", log_stamp)
    assert [p.name.endswith(f"-{log_stamp}.csv") for p in results.iterdir()] == [True]
    assert not log_file.exists(), "the configured name itself is never written"


def test_run_scan_does_not_scan_its_own_stamped_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare log_file puts the log in the scanned folder, where only the file itself is excluded."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.txt").write_text("hello")

    run_scan(
        Config(
            start_dirs=[tmp_path],
            log_file=Path("piidigger.log"),
            results=ResultsConfig(path=tmp_path / "results", formats=["csv"]),
        )
    )

    assert "Scanned: 1 folder, 1 file," in _written_log(tmp_path / "piidigger.log").read_text(encoding="utf-8")


@pytest.mark.unit
def test_stamped_log_file_leaves_a_device_alone() -> None:
    assert _stamped_log_file(Path(os.devnull), "20261031-153257") == Path(os.devnull)


@pytest.mark.unit
def test_stamped_log_file_leaves_a_symlink_alone(tmp_path: Path) -> None:
    """/dev/stdout is a symlink, whose target is a regular file when stdout is redirected to one."""
    target = tmp_path / "redirected.txt"
    target.write_text("")
    link = tmp_path / "stdout"
    link.symlink_to(target)

    assert _stamped_log_file(link, "20261031-153257") == link
