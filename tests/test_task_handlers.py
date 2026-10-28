"""Unit tests for handle_enum_dir and handle_scan_file in worker.py."""

from __future__ import annotations

import dataclasses
import functools
import multiprocessing as mp
import os
import stat
import sys
import types
import unittest.mock
from pathlib import Path

import pytest

from piidigger.models.config import Config
from piidigger.models.tasks import Task, TaskProgress, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger
from piidigger.orchestration.worker import handle_enum_dir, handle_scan_file, resolve_exclude_dirs
from piidigger.orchestration.worker._enum_dir import _is_excluded
from tests._fs import make_dir_alias

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOG_QUEUE: mp.Queue = mp.Queue()  # type: ignore[type-arg]


def _make_ctx(tmp_path: Path, **config_kwargs: object) -> WorkerContext:
    return WorkerContext(
        config=Config(start_dirs=[], **config_kwargs),  # type: ignore[arg-type]
        task_queue=mp.Queue(),
        result_queue=mp.Queue(),
        log_queue=_LOG_QUEUE,
        stop_event=mp.Event(),
    )


def _logger() -> object:
    return build_worker_logger(_LOG_QUEUE, "test-handler")


def _enum_dir_task(path: Path, depth: int = 0) -> Task:
    return Task(
        task_type=TaskType.ENUM_DIR,
        payload={"path": str(path), "depth": depth},
    )


def _scan_file_task(path: Path, ext: str = ".txt", mime: str | None = None) -> Task:
    return Task(
        task_type=TaskType.SCAN_FILE,
        payload={
            "display_path": str(path),
            "file_path": str(path),
            "ext": ext,
            "mime": mime,
            "size": path.stat().st_size if path.exists() else 0,
            "depth": 0,
        },
    )


# ---------------------------------------------------------------------------
# handle_enum_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_dir_empty_directory(tmp_path: Path) -> None:
    scan_root = tmp_path / "empty"
    scan_root.mkdir()

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(scan_root), ctx, _logger())

    assert result.status == "ok"
    assert result.counters.get("dirs_scanned") == 1
    assert result.new_tasks == []


@pytest.mark.unit
def test_enum_dir_finds_subdirs_and_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "file.txt").write_text("hello")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert result.status == "ok"
    task_types = [t["task_type"] for t in result.new_tasks]
    assert TaskType.ENUM_DIR in task_types
    assert TaskType.SCAN_FILE in task_types
    assert result.counters.get("dirs_scanned") == 1
    assert result.counters.get("dirs_found", 0) >= 1
    assert result.counters.get("files_found", 0) >= 1


@pytest.mark.unit
def test_enum_dir_respects_exclude_dirs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    excluded = root / "skip_me"
    excluded.mkdir()
    kept = root / "keep"
    kept.mkdir()

    ctx = _make_ctx(tmp_path, exclude_dirs=[str(excluded)])
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert result.status == "ok"
    task_paths = [t["payload"].get("path", "") for t in result.new_tasks]
    assert not any("skip_me" in p for p in task_paths)
    assert any("keep" in p for p in task_paths)


@pytest.mark.unit
def test_enum_dir_exclude_dirs_forward_slash_pattern(tmp_path: Path) -> None:
    """Exclude patterns with forward slashes must work on all platforms.

    The default config stores Windows paths as 'C:/Program Files' (forward
    slashes) while Path.resolve() returns backslash paths on Windows.
    _is_excluded must normalise both sides so the exclusion fires correctly.
    """
    root = tmp_path / "root"
    root.mkdir()
    excluded = root / "skip_me"
    excluded.mkdir()
    kept = root / "keep"
    kept.mkdir()

    # Simulate a config entry written with forward slashes (as the default
    # TOML template generates on Windows).
    forward_slash_pattern = str(excluded).replace("\\", "/")
    ctx = _make_ctx(tmp_path, exclude_dirs=[forward_slash_pattern])
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert result.status == "ok"
    task_paths = [t["payload"].get("path", "") for t in result.new_tasks]
    assert not any("skip_me" in p for p in task_paths), "forward-slash exclude pattern was not honoured"
    assert any("keep" in p for p in task_paths)


@pytest.mark.unit
def test_resolve_exclude_dirs_matches_pattern_written_through_alias(tmp_path: Path) -> None:
    """An exclude pattern written through a symlink alias must still match.

    Regression test: on macOS, mkdtemp() returns /var/folders/... but entries
    resolve to /private/var/...; the same applies to the /etc default.
    _is_excluded() resolves the entry, so the pattern must be resolved too.
    """
    physical = tmp_path / "physical"
    excluded = physical / "skip_me"
    excluded.mkdir(parents=True)
    (physical / "keep").mkdir()
    alias = tmp_path / "alias"
    make_dir_alias(alias, physical)

    aliased_pattern = str(alias / "skip_me")
    assert not _is_excluded(excluded, [aliased_pattern]), "unresolved alias pattern should not match"
    assert _is_excluded(excluded, resolve_exclude_dirs([aliased_pattern]))

    ctx = _make_ctx(tmp_path, exclude_dirs=resolve_exclude_dirs([aliased_pattern]))
    result = handle_enum_dir(_enum_dir_task(physical), ctx, _logger())

    task_paths = [t["payload"].get("path", "") for t in result.new_tasks]
    assert not any("skip_me" in p for p in task_paths)
    assert any("keep" in p for p in task_paths)


@pytest.mark.unit
def test_resolve_exclude_dirs_leaves_wildcard_and_relative_patterns() -> None:
    assert resolve_exclude_dirs(["*/.vscode-server", "relative/dir"]) == ["*/.vscode-server", "relative/dir"]


@pytest.mark.unit
def test_resolve_exclude_dirs_strips_trailing_separator(tmp_path: Path) -> None:
    excluded = tmp_path / "skip_me"
    excluded.mkdir()

    patterns = resolve_exclude_dirs([str(excluded) + os.sep, str(excluded).replace("\\", "/") + "/"])

    assert all(_is_excluded(excluded / "child", [p]) for p in patterns)


@pytest.mark.unit
def test_resolve_exclude_dirs_keeps_missing_paths(tmp_path: Path) -> None:
    missing = tmp_path / "not_there"

    assert _is_excluded(missing / "child", resolve_exclude_dirs([str(missing)]))


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="drive letters are Windows-only")
def test_resolve_exclude_dirs_bare_drive_means_whole_drive(tmp_path: Path) -> None:
    """'G:' must exclude the whole drive, not the current directory on it.

    os.path.realpath('C:') returns the current working directory on C:.
    """
    drive = os.path.splitdrive(str(tmp_path.resolve()))[0]

    for pattern in (drive, drive + "/", drive + "\\"):
        resolved = resolve_exclude_dirs([pattern])
        assert resolved == [drive], pattern
        assert _is_excluded(tmp_path, resolved), pattern


@pytest.mark.unit
def test_enum_dir_nonexistent_path_returns_error(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(tmp_path / "no_such_dir"), ctx, _logger())
    assert result.status == "error"
    assert result.counters.get("dirs_scanned") == 1


@pytest.mark.unit
def test_enum_dir_skips_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    link = root / "link"
    try:
        link.symlink_to(target)
    except OSError, NotImplementedError:
        pytest.skip("symlinks not supported on this platform")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    task_paths = [t["payload"].get("path", "") for t in result.new_tasks]
    assert not any("link" in Path(p).name for p in task_paths)


# ---------------------------------------------------------------------------
# handle_scan_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_file_txt_with_no_matches(tmp_path: Path) -> None:
    f = tmp_path / "plain.txt"
    f.write_text("no pii here, just lorem ipsum")

    ctx = _make_ctx(tmp_path)
    result = handle_scan_file(_scan_file_task(f), ctx, _logger())

    assert result.status == "ok"
    assert result.counters.get("files_scanned") == 1
    assert result.findings == []


@pytest.mark.unit
def test_scan_file_txt_finds_pan(tmp_path: Path) -> None:
    f = tmp_path / "pan.txt"
    f.write_text("card number: 4111111111111111")

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    result = handle_scan_file(_scan_file_task(f), ctx, _logger())

    assert result.status == "ok"
    assert result.counters.get("files_scanned") == 1
    assert len(result.findings) >= 1
    finding = result.findings[0]
    assert finding["source_path"] == str(f)
    assert finding["handler"] == "pan"


@pytest.mark.unit
def test_scan_file_no_handler_returns_error(tmp_path: Path) -> None:
    f = tmp_path / "unknown.xyz123"
    f.write_bytes(b"binary data")

    ctx = _make_ctx(tmp_path)
    result = handle_scan_file(
        Task(
            task_type=TaskType.SCAN_FILE,
            payload={
                "display_path": str(f),
                "file_path": str(f),
                "ext": ".xyz123",
                "mime": None,
                "size": f.stat().st_size,
                "depth": 0,
            },
        ),
        ctx,
        _logger(),
    )

    assert result.status == "error"
    assert result.counters.get("files_scanned") == 1


@pytest.mark.unit
def test_scan_file_testdata_pan_pdf() -> None:
    """Smoke test: scan the known-PII PDF fixture, expect at least one finding."""
    pdf = Path("testdata/pan/sample-pans.pdf")
    if not pdf.exists():
        pytest.skip("testdata not available")

    log_q: mp.Queue = mp.Queue()  # type: ignore[type-arg]
    ctx = WorkerContext(
        config=Config(start_dirs=[], data_handlers=["pan"]),
        task_queue=mp.Queue(),
        result_queue=mp.Queue(),
        log_queue=log_q,
        stop_event=mp.Event(),
    )
    task = Task(
        task_type=TaskType.SCAN_FILE,
        payload={
            "display_path": str(pdf),
            "file_path": str(pdf),
            "ext": ".pdf",
            "mime": None,
            "size": pdf.stat().st_size,
            "depth": 0,
        },
    )
    result = handle_scan_file(task, ctx, build_worker_logger(log_q, "test"))

    assert result.status == "ok"
    assert len(result.findings) >= 1


@pytest.mark.unit
def test_scan_file_permission_denied_returns_error(tmp_path: Path) -> None:
    """handle_scan_file() returns status='error' when the file cannot be opened."""
    f = tmp_path / "restricted.txt"
    f.write_text("content that cannot be read")

    ctx = _make_ctx(tmp_path)
    # The plaintext handler's first open() is the encoding sample in getencoding.
    with unittest.mock.patch(
        "piidigger.getencoding.open",
        side_effect=PermissionError("access denied"),
        create=True,
    ):
        result = handle_scan_file(_scan_file_task(f), ctx, _logger())

    assert result.status == "error"
    assert result.counters.get("files_scanned") == 1


@pytest.mark.unit
def test_scan_file_undetectable_encoding_is_logged_and_counted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A text file that was never read must show up in the log, not pass as a silent ok."""
    import logging

    f = Path("testdata/plaintext/mislabeled-text-file.txt")
    logger = logging.getLogger("tests.scan_file.encoding")

    with caplog.at_level(logging.INFO, logger=logger.name):
        result = handle_scan_file(_scan_file_task(f), _make_ctx(tmp_path), logger)

    assert result.status == "ok"
    assert result.counters == {"files_scanned": 1, "bytes_scanned": f.stat().st_size}
    assert result.findings == []
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.INFO, f"skipping {f}: text encoding could not be detected")
    ]


# ---------------------------------------------------------------------------
# _is_cloud_placeholder
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cloud_placeholder_check_skips_win32api_off_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Off Windows the answer is always False, without retrying the failed import for every file."""
    from piidigger.orchestration.worker._enum_dir import _is_cloud_placeholder

    consulted: list[str] = []
    fake = unittest.mock.MagicMock()
    fake.GetFileAttributes.side_effect = lambda p: consulted.append(p) or 0x400000
    monkeypatch.setitem(sys.modules, "win32api", fake)
    monkeypatch.setattr(os, "name", "posix")

    assert _is_cloud_placeholder(tmp_path) is False
    assert consulted == []


class _StatStub:
    def __init__(self, flags: int | None, *, size: int = 10, blocks: int = 8) -> None:
        self._flags = flags
        self._size = size
        self._blocks = blocks

    def stat(self, *, follow_symlinks: bool = True) -> object:
        if self._flags is None:
            raise OSError("gone")
        return types.SimpleNamespace(st_flags=self._flags, st_size=self._size, st_blocks=self._blocks)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("flags", "expected"),
    [(stat.SF_DATALESS, True), (stat.SF_DATALESS | stat.UF_COMPRESSED, True), (0, False), (None, False)],
)
def test_cloud_placeholder_on_macos_reads_dataless_flag(
    monkeypatch: pytest.MonkeyPatch, flags: int | None, expected: bool
) -> None:
    from piidigger.orchestration.worker._enum_dir import _is_cloud_placeholder

    monkeypatch.setattr(sys, "platform", "darwin")

    assert _is_cloud_placeholder(_StatStub(flags)) is expected  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("flags", "size", "blocks", "expected"),
    [
        (0, 168_000_000, 0, True),  # OneDrive's sparse placeholder: full size, nothing on disk
        (stat.UF_COMPRESSED, 4096, 0, False),  # APFS-compressed files also report zero blocks
        (0, 0, 0, False),  # empty file
        (0, 4096, 8, False),  # ordinary file
    ],
)
def test_cloud_placeholder_on_macos_treats_unallocated_files_as_placeholders(
    monkeypatch: pytest.MonkeyPatch, flags: int, size: int, blocks: int, expected: bool
) -> None:
    from piidigger.orchestration.worker._enum_dir import _is_cloud_placeholder

    monkeypatch.setattr(sys, "platform", "darwin")

    assert _is_cloud_placeholder(_StatStub(flags, size=size, blocks=blocks)) is expected  # type: ignore[arg-type]


@pytest.mark.unit
def test_enum_dir_skips_cloud_placeholders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import piidigger.orchestration.worker._enum_dir as enum_dir_mod

    root = tmp_path / "root"
    root.mkdir()
    (root / "local.txt").write_text("here")
    (root / "cloud.txt").write_text("not really here")
    monkeypatch.setattr(enum_dir_mod, "_is_cloud_placeholder", lambda path: path.name == "cloud.txt")

    result = handle_enum_dir(_enum_dir_task(root), _make_ctx(tmp_path), _logger())

    names = [Path(t["payload"]["file_path"]).name for t in result.new_tasks]
    assert names == ["local.txt"]


@pytest.mark.unit
@pytest.mark.parametrize(("local_only", "expected"), [(True, ["local"]), (False, ["local", "nfs"])])
def test_enum_dir_skips_network_filesystems_when_local_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local_only: bool, expected: list[str]
) -> None:
    import piidigger.orchestration.worker._enum_dir as enum_dir_mod

    root = tmp_path / "root"
    (root / "local").mkdir(parents=True)
    (root / "nfs").mkdir()
    monkeypatch.setattr(enum_dir_mod, "is_remote_filesystem", lambda path, dev: path.endswith("nfs"))

    result = handle_enum_dir(_enum_dir_task(root), _make_ctx(tmp_path, local_files_only=local_only), _logger())

    assert sorted(Path(t["payload"]["path"]).name for t in result.new_tasks) == expected


@pytest.mark.unit
def test_enum_dir_retries_a_listing_interrupted_by_a_signal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")
    real_iterdir = Path.iterdir
    calls: list[Path] = []

    def flaky_iterdir(self: Path):  # type: ignore[no-untyped-def]
        calls.append(self)
        if len(calls) == 1:
            raise InterruptedError(4, "Interrupted system call")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

    result = handle_enum_dir(_enum_dir_task(root), _make_ctx(tmp_path), _logger())

    assert result.status == "ok"
    assert len(calls) == 2
    assert [Path(t["payload"]["file_path"]).name for t in result.new_tasks] == ["a.txt"]


@pytest.mark.unit
def test_enum_dir_reports_alive_while_listing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import piidigger.orchestration.worker._enum_dir as enum_dir_mod

    root = tmp_path / "root"
    root.mkdir()
    for i in range(3):
        (root / f"f{i}.txt").write_text("x")
    sent: list[object] = []
    ctx = _make_ctx(tmp_path)
    ctx = dataclasses.replace(ctx, result_queue=types.SimpleNamespace(put=sent.append))  # type: ignore[arg-type]
    monkeypatch.setattr(
        enum_dir_mod, "ProgressReporter", functools.partial(enum_dir_mod.ProgressReporter, alive_interval=0.0)
    )
    task = _enum_dir_task(root)

    handle_enum_dir(task, ctx, _logger())

    alive = [m for m in sent if isinstance(m, TaskProgress) and m.event == "alive"]
    assert len(alive) == 3
    assert {m.task_id for m in alive} == {task.task_id}


@pytest.mark.unit
def test_scan_file_error_message_names_the_exception_type(tmp_path: Path) -> None:
    """str() of a KeyError is only the key, which on its own tells the reader nothing."""
    f = tmp_path / "broken.txt"
    f.write_text("content")

    with unittest.mock.patch("piidigger.getencoding.open", side_effect=KeyError("7"), create=True):
        result = handle_scan_file(_scan_file_task(f), _make_ctx(tmp_path), _logger())

    assert result.status == "error"
    assert result.error_message == "KeyError: '7'"
