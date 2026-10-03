"""Multi-format archive support tests (ZIP, 7z, and tar).

Covers:
  - ArchiveConfig model (defaults, TOML round-trip, unknown-key rejection)
  - ArchiveMemberItem (protocol compliance, open_bytes, open_stream, materialize)
  - FilesystemItem.open_bytes() still returns None
  - secure_delete() utility
  - _cleanup_temp_workspace() recursive deletion
  - handle_enum_dir archive routing (zip/7z/tar → ENUM_ARCHIVE_MEMBERS; archives disabled)
  - detect_archive_type() compound-suffix and alias mapping
  - handle_enum_archive_members safety checks (ZIP, 7z, and tar scenarios)
  - handle_scan_archive_members (PII found, lineage fields, no-handler defensive path)
  - --no-archives CLI override logic
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from piidigger.models.config import ArchiveConfig, Config
from piidigger.models.tasks import Task, TaskProgress, TaskResult, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger
from piidigger.orchestration.secure_delete import secure_delete
from piidigger.orchestration.sources import FilesystemItem
from piidigger.orchestration.worker._enum_archive import handle_enum_archive_members
from piidigger.orchestration.worker._enum_dir import handle_enum_dir
from piidigger.orchestration.worker._scan_archive_members import handle_scan_archive_members
from piidigger.protocols import ScannableItem

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LOG_QUEUE: mp.Queue = mp.Queue()  # type: ignore[type-arg]
_ZIP_FIXTURES = Path("testdata/zip")
_7Z_FIXTURES = Path("testdata/7z")
_TAR_FIXTURES = Path("testdata/tar")


def _logger() -> logging.Logger:
    return build_worker_logger(_LOG_QUEUE, "test-archives")  # type: ignore[no-any-return]


def _fixture(name: str) -> Path:
    p = _ZIP_FIXTURES / name
    if not p.exists():
        pytest.skip(f"fixture {p} not found — run testdata/zip/create_fixtures.py")
    return p


def _7z_fixture(name: str) -> Path:
    p = _7Z_FIXTURES / name
    if not p.exists():
        pytest.skip(f"fixture {p} not found — run testdata/7z/create_fixtures.py")
    return p


def _tar_fixture(name: str) -> Path:
    p = _TAR_FIXTURES / name
    if not p.exists():
        pytest.skip(f"fixture {p} not found — run testdata/tar/create_fixtures.py")
    return p


def _make_ctx(
    tmp_path: Path,
    *,
    archives: dict[str, Any] | None = None,
    data_handlers: list[str] | None = None,
    n_workers: int = 1,
) -> WorkerContext:
    """A context whose result_queue is an in-process queue, so tests can read TaskProgress back at once."""
    arc_cfg = ArchiveConfig(**(archives or {}))
    extra: dict[str, Any] = {}
    if data_handlers is not None:
        extra["data_handlers"] = data_handlers
    return WorkerContext(
        config=Config(start_dirs=[], archives=arc_cfg, **extra),
        task_queue=mp.Queue(),
        result_queue=queue.Queue(),  # type: ignore[arg-type]
        log_queue=_LOG_QUEUE,
        stop_event=mp.Event(),
        temp_base=tmp_path,
        n_workers=n_workers,
    )


def _drain(ctx: WorkerContext) -> list[Any]:
    messages = []
    while True:
        try:
            messages.append(ctx.result_queue.get_nowait())
        except queue.Empty:
            return messages


@dataclass
class _ScanOutcome:
    """A batch scan's TaskResult plus what it streamed, summed the way the coordinator sums it."""

    result: TaskResult
    progress: list[TaskProgress]

    @property
    def status(self) -> str:
        return self.result.status

    @property
    def done(self) -> list[TaskProgress]:
        return [p for p in self.progress if p.event == "item_done"]

    @property
    def findings(self) -> list[dict[str, Any]]:
        return [f for p in self.done for f in p.findings]

    @property
    def counters(self) -> dict[str, int]:
        total: Counter[str] = Counter(self.result.counters)
        for p in self.done:
            total.update(p.counters)
        return dict(total)


def _scan(task: Task, ctx: WorkerContext) -> _ScanOutcome:
    result = handle_scan_archive_members(task, ctx, _logger())
    return _ScanOutcome(result, [m for m in _drain(ctx) if isinstance(m, TaskProgress)])


def _member_paths(result: TaskResult) -> list[str]:
    """Every member path an enumeration result queued for scanning, across all batches, in order."""
    return [member for t in result.new_tasks for member in t["items"]]


def _enum_archive_task(archive_path: Path, depth: int = 0, archive_type: str = "zip") -> Task:
    return Task(
        task_type=TaskType.ENUM_ARCHIVE_MEMBERS,
        payload={"archive_path": str(archive_path), "archive_type": archive_type, "depth": depth},
    )


def _scan_archive_task(
    archive_path: Path,
    *members: str,
    depth: int = 1,
    archive_type: str = "zip",
) -> Task:
    return Task(
        task_type=TaskType.SCAN_ARCHIVE_MEMBERS,
        payload={"archive_path": str(archive_path), "archive_type": archive_type, "depth": depth},
        items=members,
    )


def _enum_dir_task(path: Path) -> Task:
    return Task(
        task_type=TaskType.ENUM_DIR,
        payload={"path": str(path), "depth": 0},
    )


# ---------------------------------------------------------------------------
# ArchiveConfig model
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_archive_config_defaults() -> None:
    cfg = ArchiveConfig()
    assert cfg.enabled is True
    assert cfg.formats == ["all"]
    assert cfg.max_depth == 1
    assert cfg.max_members == 10_000
    assert cfg.max_member_uncompressed_size_mb == 512
    assert cfg.max_total_uncompressed_size_mb == 8192


@pytest.mark.unit
def test_archive_config_from_toml(tmp_path: Path) -> None:
    toml = tmp_path / "cfg.toml"
    toml.write_text(
        "[archives]\nenabled = false\nmax_members = 500\nmax_member_uncompressed_size_mb = 128\n",
        encoding="utf-8",
    )
    config = Config.from_toml(toml)
    assert config.archives.enabled is False
    assert config.archives.max_members == 500
    assert config.archives.max_member_uncompressed_size_mb == 128
    assert config.archives.max_total_uncompressed_size_mb == 8192  # default preserved


@pytest.mark.unit
def test_archive_config_unknown_key_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ArchiveConfig(typo_field="bad")


@pytest.mark.unit
def test_archive_config_unknown_toml_key_raises(tmp_path: Path) -> None:
    toml = tmp_path / "bad.toml"
    toml.write_text("[archives]\nenabled = true\ntypo_field = 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Config.from_toml(toml)


@pytest.mark.unit
def test_archive_config_rejects_unknown_format() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="unknown archive format"):
        ArchiveConfig(formats=["zip", "rar"])


@pytest.mark.unit
def test_archive_config_accepts_known_formats_case_insensitively() -> None:
    """Mixed case validates AND is normalized to lowercase.

    _enum_dir matches archive_type against these names; normalizing here means
    the comparison no longer depends on that consumer lowercasing too.
    """
    cfg = ArchiveConfig(formats=["ZIP", "Tar"])
    assert cfg.formats == ["zip", "tar"]


@pytest.mark.unit
def test_archive_config_all_bypasses_format_validation() -> None:
    cfg = ArchiveConfig(formats=["all"])
    assert cfg.formats == ["all"]


@pytest.mark.unit
def test_generate_toml_template_includes_archives_section() -> None:
    from piidigger.models.config import generate_toml_template

    template = generate_toml_template()
    assert "[archives]" in template


# ---------------------------------------------------------------------------
# FilesystemItem — archive context display_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_filesystem_item_display_path_with_archive_context(tmp_path: Path) -> None:
    """display_path returns archive::member form when archive context is set."""
    f = tmp_path / "member.txt"
    f.write_bytes(b"x")
    archive = tmp_path / "archive.zip"
    item = FilesystemItem(f, archive_path=archive, member_path="sub/member.txt")
    assert item.display_path == f"{archive}::sub/member.txt"


@pytest.mark.unit
def test_filesystem_item_display_path_without_archive_context(tmp_path: Path) -> None:
    """display_path returns plain file path when no archive context is set."""
    f = tmp_path / "plain.txt"
    f.write_bytes(b"x")
    item = FilesystemItem(f)
    assert item.display_path == str(f)


@pytest.mark.unit
def test_filesystem_item_satisfies_protocol(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_bytes(b"hello")
    assert isinstance(FilesystemItem(f), ScannableItem)


# ---------------------------------------------------------------------------
# FilesystemItem.open_bytes() still returns None
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_filesystem_item_open_bytes_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_bytes(b"hello")
    assert FilesystemItem(f).open_bytes() is None


# ---------------------------------------------------------------------------
# secure_delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_secure_delete_removes_file(tmp_path: Path) -> None:
    f = tmp_path / "sensitive.txt"
    f.write_bytes(b"secret data" * 100)
    assert f.exists()
    secure_delete(f)
    assert not f.exists()


@pytest.mark.unit
def test_secure_delete_nonexistent_path_does_not_raise(tmp_path: Path) -> None:
    secure_delete(tmp_path / "does_not_exist.txt")  # must not raise


@pytest.mark.unit
def test_secure_delete_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_bytes(b"")
    secure_delete(f)
    assert not f.exists()


# ---------------------------------------------------------------------------
# handle_enum_dir archive routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_dir_zip_file_emits_enum_archive_task(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.zip").write_bytes(b"placeholder")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert result.status == "ok"
    task_types = {t["task_type"] for t in result.new_tasks}
    assert TaskType.ENUM_ARCHIVE_MEMBERS in task_types
    assert TaskType.SCAN_FILE not in task_types


@pytest.mark.unit
def test_enum_dir_zip_skipped_when_archives_disabled(tmp_path: Path) -> None:
    """With archives.enabled=False, no ENUM_ARCHIVE_MEMBERS task is emitted for .zip files."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.zip").write_bytes(b"placeholder")

    ctx = _make_ctx(tmp_path, archives={"enabled": False})
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert result.status == "ok"
    task_types = {t["task_type"] for t in result.new_tasks}
    # Core assertion: disabled archives must never emit ENUM_ARCHIVE_MEMBERS
    assert TaskType.ENUM_ARCHIVE_MEMBERS not in task_types


@pytest.mark.unit
def test_enum_dir_txt_file_unaffected_by_archive_config(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.txt").write_text("hello")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    task_types = {t["task_type"] for t in result.new_tasks}
    assert TaskType.SCAN_FILE in task_types


@pytest.mark.unit
def test_enum_dir_zip_counts_in_files_found(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.zip").write_bytes(b"placeholder")
    (root / "b.zip").write_bytes(b"placeholder")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert result.counters.get("files_found", 0) == 2


# ---------------------------------------------------------------------------
# handle_enum_archive_members — safety checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_archive_simple_pii_emits_scan_task(tmp_path: Path) -> None:
    archive = _fixture("simple-pii.zip")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    assert len(result.new_tasks) == 1
    t = result.new_tasks[0]
    assert t["task_type"] == TaskType.SCAN_ARCHIVE_MEMBERS
    assert t["items"] == ["readme.txt"]
    assert result.counters.get("files_found") == 1
    assert result.counters.get("archive_members_skipped", 0) == 0


@pytest.mark.unit
def test_enum_archive_corrupt_returns_error(tmp_path: Path) -> None:
    archive = _fixture("corrupt.zip")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "error"
    assert result.counters.get("archive_errors", 0) >= 1


@pytest.mark.unit
def test_enum_archive_traversal_member_rejected(tmp_path: Path) -> None:
    archive = _fixture("traversal-member.zip")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1
    assert result.counters.get("archive_errors", 0) >= 1  # traversal counts as an error


@pytest.mark.unit
def test_enum_archive_encrypted_member_skipped(tmp_path: Path) -> None:
    archive = _fixture("encrypted-member.zip")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1


@pytest.mark.unit
def test_enum_archive_oversize_member_skipped(tmp_path: Path) -> None:
    """200 MB member exceeds max_member_uncompressed_size_mb=50 (default)."""
    archive = _fixture("oversize-member.zip")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1


@pytest.mark.unit
def test_enum_archive_bomb_ratio_rejected(tmp_path: Path) -> None:
    """Member with ratio > 1000:1 is rejected (check 5)."""
    archive = _fixture("zip-bomb-simulated.zip")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1
    assert result.counters.get("archive_errors", 0) >= 1  # bomb rejection counts as error


@pytest.mark.unit
def test_enum_archive_member_count_limit(tmp_path: Path) -> None:
    """5-member archive with max_members=3 → 3 members queued, 2 skipped."""
    archive = _fixture("many-members.zip")
    ctx = _make_ctx(tmp_path, archives={"max_members": 3})
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    assert len(_member_paths(result)) == 3
    assert result.counters.get("archive_members_skipped", 0) == 2


@pytest.mark.unit
def test_enum_archive_nested_zip_skipped(tmp_path: Path) -> None:
    """nested-depth-2.zip has outer.txt (accepted) + inner.zip (skipped as nested)."""
    archive = _fixture("nested-depth-2.zip")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    # outer.txt should emit a task; inner.zip should be skipped
    assert len(result.new_tasks) == 1
    assert result.new_tasks[0]["items"] == ["outer.txt"]
    assert result.counters.get("archive_members_skipped", 0) == 1


@pytest.mark.unit
def test_enum_archive_no_handler_member_skipped(tmp_path: Path) -> None:
    """A member with an unknown extension is skipped (no registered handler)."""
    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("data.xyz123unknownext", "some data")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(zp), ctx, _logger())

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1


@pytest.mark.unit
def test_enum_archive_zip_symlink_member_not_scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Symlink members excluded by list_members() are never scanned."""
    from piidigger.archivehandlers import _zip

    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("readme.txt", "hello world")
        zf.writestr("link-to-readme.txt", "readme.txt")

    monkeypatch.setattr(_zip, "_is_symlink", lambda info: info.filename == "link-to-readme.txt")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(_enum_archive_task(zp), ctx, _logger())

    assert result.status == "ok"
    member_paths = _member_paths(result)
    assert "link-to-readme.txt" not in member_paths


@pytest.mark.unit
def test_enum_archive_total_size_limit_skips_oversized(tmp_path: Path) -> None:
    """Members that push running total over the limit are skipped."""
    zp = tmp_path / "big.zip"
    # Create a 1MB member so total limit at 1MB will reject it
    with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("file1.txt", "a" * (1 * 1024 * 1024 + 1))  # slightly over 1MB

    # max_total_uncompressed_size_mb=1 — this member exceeds it
    ctx = _make_ctx(tmp_path, archives={"max_total_uncompressed_size_mb": 1})
    result = handle_enum_archive_members(_enum_archive_task(zp), ctx, _logger())

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1


# ---------------------------------------------------------------------------
# handle_scan_archive_members
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_archive_member_finds_pii(tmp_path: Path) -> None:
    archive = _fixture("simple-pii.zip")

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt")
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert result.counters.get("files_scanned") == 1
    assert len(result.findings) >= 1
    assert result.findings[0]["handler"] == "pan"


@pytest.mark.unit
def test_scan_archive_member_no_pii(tmp_path: Path) -> None:
    zp = tmp_path / "clean.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("notes.txt", "nothing sensitive here, just lorem ipsum")

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(zp, "notes.txt")
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert result.findings == []
    assert result.counters.get("files_scanned") == 1


@pytest.mark.unit
def test_scan_archive_member_result_lineage(tmp_path: Path) -> None:
    """ResultRecord lineage fields are populated correctly."""
    archive = _fixture("simple-pii.zip")

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", depth=1)
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert len(result.findings) >= 1
    finding = result.findings[0]
    assert finding["source_path"] == str(archive)
    assert finding["source_member_path"] == "readme.txt"
    assert finding["source_depth"] == 1
    assert finding["source_container_type"] == "zip"


@pytest.mark.unit
def test_scan_archive_member_bytes_scanned_counter(tmp_path: Path) -> None:
    zp = tmp_path / "counter.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("f.txt", "some content")
    with zipfile.ZipFile(zp) as zf:
        size = zf.getinfo("f.txt").file_size

    ctx = _make_ctx(tmp_path)
    task = _scan_archive_task(zp, "f.txt")
    result = _scan(task, ctx)

    assert result.counters.get("bytes_scanned") == size


@pytest.mark.unit
def test_scan_archive_member_unknown_handler_fails_that_member_only(tmp_path: Path) -> None:
    """Defensive: a member that reaches scan with no handler is reported failed; the batch goes on."""
    zp = tmp_path / "noh.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("file.xyz999", "data")
        zf.writestr("after.txt", "card number: 4111111111111111")

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(zp, "file.xyz999", "after.txt")
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert [p.item for p in result.done] == ["file.xyz999", "after.txt"]
    assert result.done[0].counters == {"files_scanned": 1, "tasks_failed": 1}
    assert result.counters.get("files_scanned") == 2
    assert result.counters.get("tasks_failed") == 1
    assert result.findings[0]["source_member_path"] == "after.txt"


# ---------------------------------------------------------------------------
# --no-archives CLI override logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_archives_flag_disables_archives_on_config() -> None:
    """Simulate the --no-archives CLI flag: model_copy chain overrides just enabled."""
    config = Config.default()
    assert config.archives.enabled is True

    updated = config.model_copy(
        update={"archives": config.archives.model_copy(update={"enabled": False})}
    )
    assert updated.archives.enabled is False
    # Other archive fields are preserved
    assert updated.archives.max_members == config.archives.max_members
    assert updated.archives.formats == config.archives.formats


@pytest.mark.unit
def test_no_archives_flag_does_not_mutate_original() -> None:
    """model_copy must not mutate the original Config (immutability check)."""
    config = Config.default()
    _ = config.model_copy(
        update={"archives": config.archives.model_copy(update={"enabled": False})}
    )
    assert config.archives.enabled is True


# ---------------------------------------------------------------------------
# 7z handler — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_7z_handler_list_members(tmp_path: Path) -> None:
    import io

    import py7zr

    from piidigger.archivehandlers._7z import handler as szhandler

    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(b"hello world", "greeting.txt")
    archive = tmp_path / "test.7z"
    archive.write_bytes(buf.getvalue())

    members = szhandler.list_members(archive)
    assert len(members) == 1
    assert members[0].name == "greeting.txt"
    assert members[0].uncompressed_size == 11
    assert members[0].is_dir is False
    assert members[0].is_encrypted is False


@pytest.mark.unit
def test_7z_handler_list_members_excludes_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """py7zr's in-memory writer can't author a real symlink entry, so the
    symlink/non-symlink distinction is exercised by monkeypatching the
    handler's own _is_symlink() rather than crafting a malicious archive."""
    import io

    import py7zr

    from piidigger.archivehandlers import _7z

    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(b"hello world", "readme.txt")
        szf.writestr(b"readme.txt", "link-to-readme.txt")
    archive = tmp_path / "test.7z"
    archive.write_bytes(buf.getvalue())

    monkeypatch.setattr(_7z, "_is_symlink", lambda info: info.filename == "link-to-readme.txt")

    members = _7z.handler.list_members(archive)
    names = [m.name for m in members]
    assert "readme.txt" in names
    assert "link-to-readme.txt" not in names


@pytest.mark.unit
def test_7z_handler_extract_members(tmp_path: Path) -> None:
    import io

    import py7zr

    from piidigger.archivehandlers._7z import handler as szhandler

    payload = b"the quick brown fox"
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(payload, "fox.txt")
    archive = tmp_path / "fox.7z"
    archive.write_bytes(buf.getvalue())

    dest_dir = tmp_path / "out"
    got: dict[str, bytes] = {}
    szhandler.extract_members(
        archive, ["fox.txt"], dest_dir, on_extracted=lambda member, path: got.update({member: path.read_bytes()})
    )
    assert got == {"fox.txt": payload}


@pytest.mark.unit
def test_zip_handler_extract_members(tmp_path: Path) -> None:
    from piidigger.archivehandlers._zip import handler as ziphandler

    payload = b"zip extract content"
    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("data.txt", payload)

    dest_dir = tmp_path / "out"
    got: dict[str, Path] = {}
    ziphandler.extract_members(zp, ["data.txt"], dest_dir, on_extracted=lambda member, path: got.update({member: path}))
    assert got["data.txt"].read_bytes() == payload
    assert got["data.txt"].parent == dest_dir


@pytest.mark.unit
def test_zip_handler_list_members_excludes_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercised via monkeypatch rather than crafting real Unix symlink mode
    bits into a zip's external_attr — see test_zip_handler_is_symlink_* for
    direct coverage of that bit-math."""
    from piidigger.archivehandlers import _zip

    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("readme.txt", "hello world")
        zf.writestr("link-to-readme.txt", "readme.txt")

    monkeypatch.setattr(_zip, "_is_symlink", lambda info: info.filename == "link-to-readme.txt")

    members = _zip.handler.list_members(zp)
    names = [m.name for m in members]
    assert "readme.txt" in names
    assert "link-to-readme.txt" not in names


@pytest.mark.unit
def test_zip_handler_is_symlink_detects_unix_mode_bits() -> None:
    """Direct check of the external_attr bit-math, independent of monkeypatching."""
    from piidigger.archivehandlers._zip import _is_symlink

    symlink_info = zipfile.ZipInfo("link.txt")
    symlink_info.create_system = 3  # Unix
    symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert _is_symlink(symlink_info) is True

    regular_info = zipfile.ZipInfo("readme.txt")
    regular_info.create_system = 3  # Unix
    regular_info.external_attr = (stat.S_IFREG | 0o644) << 16
    assert _is_symlink(regular_info) is False

    windows_info = zipfile.ZipInfo("readme.txt")
    windows_info.create_system = 0  # Windows/FAT — external_attr isn't a unix mode
    windows_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert _is_symlink(windows_info) is False


@pytest.mark.unit
def test_7z_handler_corrupt_raises_archive_read_error(tmp_path: Path) -> None:
    from piidigger.archivehandlers._7z import handler as szhandler
    from piidigger.exceptions import ArchiveReadError

    corrupt = tmp_path / "bad.7z"
    corrupt.write_bytes(b"not a 7z file at all")

    with pytest.raises(ArchiveReadError):
        szhandler.list_members(corrupt)


# ---------------------------------------------------------------------------
# handle_enum_archive_members — 7z scenarios
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_archive_7z_simple_pii_emits_scan_task(tmp_path: Path) -> None:
    archive = _7z_fixture("simple-pii.7z")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="7z"), ctx, _logger()
    )

    assert result.status == "ok"
    assert len(result.new_tasks) == 1
    t = result.new_tasks[0]
    assert t["task_type"] == TaskType.SCAN_ARCHIVE_MEMBERS
    assert t["items"] == ["readme.txt"]
    assert t["payload"]["archive_type"] == "7z"
    assert result.counters.get("files_found") == 1


@pytest.mark.unit
def test_enum_archive_7z_corrupt_returns_error(tmp_path: Path) -> None:
    archive = _7z_fixture("corrupt.7z")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="7z"), ctx, _logger()
    )

    assert result.status == "error"
    assert result.counters.get("archive_errors", 0) >= 1


@pytest.mark.unit
def test_enum_archive_7z_encrypted_skipped(tmp_path: Path) -> None:
    archive = _7z_fixture("encrypted.7z")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="7z"), ctx, _logger()
    )

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1


@pytest.mark.unit
def test_enum_archive_7z_oversize_member_skipped(tmp_path: Path) -> None:
    """100 MB member exceeds default 64 MB limit."""
    archive = _7z_fixture("oversize-member.7z")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="7z"), ctx, _logger()
    )

    assert result.status == "ok"
    assert result.new_tasks == []
    assert result.counters.get("archive_members_skipped", 0) >= 1


@pytest.mark.unit
def test_enum_archive_7z_member_count_limit(tmp_path: Path) -> None:
    """5-member archive with max_members=3 → 3 members queued, 2 skipped."""
    archive = _7z_fixture("many-members.7z")
    ctx = _make_ctx(tmp_path, archives={"max_members": 3})
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="7z"), ctx, _logger()
    )

    assert result.status == "ok"
    assert len(_member_paths(result)) == 3
    assert result.counters.get("archive_members_skipped", 0) == 2


@pytest.mark.unit
def test_enum_archive_7z_symlink_member_not_scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Symlink members excluded by list_members() are never scanned."""
    import io

    import py7zr

    from piidigger.archivehandlers import _7z

    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(b"hello world", "readme.txt")
        szf.writestr(b"readme.txt", "link-to-readme.txt")
    archive = tmp_path / "test.7z"
    archive.write_bytes(buf.getvalue())

    monkeypatch.setattr(_7z, "_is_symlink", lambda info: info.filename == "link-to-readme.txt")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="7z"), ctx, _logger()
    )

    assert result.status == "ok"
    member_paths = _member_paths(result)
    assert "link-to-readme.txt" not in member_paths


@pytest.mark.unit
def test_enum_archive_unknown_type_returns_error(tmp_path: Path) -> None:
    """Requesting an unregistered archive_type returns status=error."""
    fake = tmp_path / "fake.rar"
    fake.write_bytes(b"placeholder")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(fake, archive_type="rar"), ctx, _logger()
    )

    assert result.status == "error"
    assert result.counters.get("archive_errors", 0) >= 1


# ---------------------------------------------------------------------------
# handle_scan_archive_members — 7z
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_archive_member_7z_finds_pii(tmp_path: Path) -> None:
    import io

    import py7zr

    content = b"card number: 4111111111111111\n"
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(content, "readme.txt")
    archive = tmp_path / "pii.7z"
    archive.write_bytes(buf.getvalue())

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", archive_type="7z")
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert len(result.findings) >= 1
    assert result.findings[0]["handler"] == "pan"


@pytest.mark.unit
def test_scan_archive_member_7z_lineage(tmp_path: Path) -> None:
    """source_container_type is propagated from archive_type."""
    import io

    import py7zr

    content = b"card number: 4111111111111111\n"
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(content, "readme.txt")
    archive = tmp_path / "lineage.7z"
    archive.write_bytes(buf.getvalue())

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", depth=1, archive_type="7z")
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert result.findings[0]["source_container_type"] == "7z"


# ---------------------------------------------------------------------------
# handle_enum_dir — 7z routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_dir_7z_file_emits_enum_archive_task(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.7z").write_bytes(b"placeholder")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert result.status == "ok"
    task_types = {t["task_type"] for t in result.new_tasks}
    assert TaskType.ENUM_ARCHIVE_MEMBERS in task_types

    archive_tasks = [t for t in result.new_tasks if t["task_type"] == TaskType.ENUM_ARCHIVE_MEMBERS]
    assert archive_tasks[0]["payload"]["archive_type"] == "7z"


@pytest.mark.unit
def test_enum_dir_archive_type_in_payload(tmp_path: Path) -> None:
    """archive_type in the ENUM_ARCHIVE_MEMBERS payload matches the file extension."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "backup.zip").write_bytes(b"z")
    (root / "archive.7z").write_bytes(b"z")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    archive_tasks = [t for t in result.new_tasks if t["task_type"] == TaskType.ENUM_ARCHIVE_MEMBERS]
    types_by_ext = {
        Path(t["payload"]["archive_path"]).suffix: t["payload"]["archive_type"]
        for t in archive_tasks
    }
    assert types_by_ext[".zip"] == "zip"
    assert types_by_ext[".7z"] == "7z"


# ---------------------------------------------------------------------------
# detect_archive_type() — compound-suffix and alias mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detect_archive_type_tar() -> None:
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("backup.tar") == "tar"


@pytest.mark.unit
def test_detect_archive_type_tar_gz_compound_suffix() -> None:
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("backup.tar.gz") == "tar"


@pytest.mark.unit
def test_detect_archive_type_tgz_alias() -> None:
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("backup.tgz") == "tar"


@pytest.mark.unit
def test_detect_archive_type_tbz2_alias() -> None:
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("backup.tbz2") == "tar"


@pytest.mark.unit
def test_detect_archive_type_txz_alias() -> None:
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("backup.txz") == "tar"


@pytest.mark.unit
def test_detect_archive_type_plain_gz_not_tar() -> None:
    """A bare .gz file (no .tar segment) must not be routed to the tar handler."""
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("notes.gz") is None


@pytest.mark.unit
def test_detect_archive_type_zip_unaffected() -> None:
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("archive.zip") == "zip"


@pytest.mark.unit
def test_detect_archive_type_unknown_returns_none() -> None:
    from piidigger.archivehandlers import detect_archive_type

    assert detect_archive_type("data.rar") is None


# ---------------------------------------------------------------------------
# _cleanup_temp_workspace — recursive deletion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cleanup_temp_workspace_recursive(tmp_path: Path) -> None:
    """Files in subdirectories are secure-deleted and the full tree is removed."""
    from piidigger.orchestration.worker._loop import _cleanup_temp_workspace

    task_id = "test-task-id"
    task_temp = tmp_path / task_id
    subdir = task_temp / "subdir"
    subdir.mkdir(parents=True)
    nested_file = subdir / "secret.txt"
    nested_file.write_bytes(b"sensitive data")
    flat_file = task_temp / "flat.txt"
    flat_file.write_bytes(b"also sensitive")

    _cleanup_temp_workspace(tmp_path, task_id)

    assert not task_temp.exists()


# ---------------------------------------------------------------------------
# TarArchiveHandler — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tar_handler_list_members() -> None:
    from piidigger.archivehandlers._tar import handler

    archive = _tar_fixture("simple-pii.tar")
    members = handler.list_members(archive)

    assert len(members) == 1
    assert members[0].name == "readme.txt"
    assert members[0].uncompressed_size > 0
    assert members[0].compressed_size == 0
    assert not members[0].is_dir
    assert not members[0].is_encrypted


@pytest.mark.unit
def test_tar_handler_extract_members(tmp_path: Path) -> None:
    from piidigger.archivehandlers._tar import handler

    archive = _tar_fixture("simple-pii.tar")
    got: dict[str, bytes] = {}
    handler.extract_members(
        archive, ["readme.txt"], tmp_path, on_extracted=lambda member, path: got.update({member: path.read_bytes()})
    )

    assert b"4111111111111111" in got["readme.txt"]


@pytest.mark.unit
def test_tar_handler_corrupt_raises_archive_read_error() -> None:
    from piidigger.archivehandlers._tar import handler
    from piidigger.exceptions import ArchiveReadError

    archive = _tar_fixture("corrupt.tar")
    with pytest.raises(ArchiveReadError):
        handler.list_members(archive)


@pytest.mark.unit
def test_tar_handler_list_members_excludes_symlinks() -> None:
    from piidigger.archivehandlers._tar import handler

    archive = _tar_fixture("symlink-member.tar")
    members = handler.list_members(archive)

    names = [m.name for m in members]
    assert "readme.txt" in names
    assert "link-to-readme.txt" not in names


@pytest.mark.unit
def test_tar_handler_transparent_gzip() -> None:
    from piidigger.archivehandlers._tar import handler

    archive = _tar_fixture("simple-pii.tar.gz")
    members = handler.list_members(archive)

    assert any(m.name == "readme.txt" for m in members)


@pytest.mark.unit
def test_tar_handler_transparent_bzip2() -> None:
    from piidigger.archivehandlers._tar import handler

    archive = _tar_fixture("simple-pii.tar.bz2")
    members = handler.list_members(archive)

    assert any(m.name == "readme.txt" for m in members)


@pytest.mark.unit
def test_tar_handler_transparent_xz() -> None:
    from piidigger.archivehandlers._tar import handler

    archive = _tar_fixture("simple-pii.tar.xz")
    members = handler.list_members(archive)

    assert any(m.name == "readme.txt" for m in members)


# ---------------------------------------------------------------------------
# handle_enum_archive_members — tar
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_archive_tar_simple_pii_emits_scan_task(tmp_path: Path) -> None:
    archive = _tar_fixture("simple-pii.tar")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    assert len(result.new_tasks) == 1
    assert result.new_tasks[0]["items"] == ["readme.txt"]
    assert result.new_tasks[0]["payload"]["archive_type"] == "tar"


@pytest.mark.unit
def test_enum_archive_tar_gz_simple_pii_emits_scan_task(tmp_path: Path) -> None:
    archive = _tar_fixture("simple-pii.tar.gz")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    assert len(result.new_tasks) == 1


@pytest.mark.unit
def test_enum_archive_tar_corrupt_returns_error(tmp_path: Path) -> None:
    archive = _tar_fixture("corrupt.tar")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "error"


@pytest.mark.unit
def test_enum_archive_tar_oversize_member_skipped(tmp_path: Path) -> None:
    archive = _tar_fixture("oversize-member.tar.gz")
    ctx = _make_ctx(tmp_path, archives={"max_member_uncompressed_size_mb": 1})
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    assert len(result.new_tasks) == 0
    assert result.counters.get("archive_members_skipped", 0) >= 1


@pytest.mark.unit
def test_enum_archive_tar_member_count_limit(tmp_path: Path) -> None:
    archive = _tar_fixture("many-members.tar")
    ctx = _make_ctx(tmp_path, archives={"max_members": 3})
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    assert len(_member_paths(result)) == 3
    assert result.counters.get("archive_members_skipped", 0) == 2


@pytest.mark.unit
def test_enum_archive_tar_traversal_member_rejected(tmp_path: Path) -> None:
    archive = _tar_fixture("traversal-member.tar")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    member_paths = _member_paths(result)
    assert not any(".." in p for p in member_paths)


@pytest.mark.unit
def test_enum_archive_tar_symlink_member_not_scanned(tmp_path: Path) -> None:
    """Symlink members excluded by list_members() are never scanned."""
    archive = _tar_fixture("symlink-member.tar")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    member_paths = _member_paths(result)
    assert "link-to-readme.txt" not in member_paths


# ---------------------------------------------------------------------------
# handle_scan_archive_members — tar
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_archive_member_tar_finds_pii(tmp_path: Path) -> None:
    archive = _tar_fixture("simple-pii.tar")
    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", archive_type="tar")
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert len(result.findings) >= 1
    assert result.findings[0]["handler"] == "pan"


@pytest.mark.unit
def test_scan_archive_member_tar_lineage(tmp_path: Path) -> None:
    """source_container_type is 'tar' regardless of compression flavor."""
    archive = _tar_fixture("simple-pii.tar.gz")
    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", depth=1, archive_type="tar")
    result = _scan(task, ctx)

    assert result.status == "ok"
    assert result.findings[0]["source_container_type"] == "tar"


# ---------------------------------------------------------------------------
# handle_enum_dir — tar routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enum_dir_tar_file_emits_enum_archive_task(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.tar").write_bytes(b"placeholder")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    archive_tasks = [t for t in result.new_tasks if t["task_type"] == TaskType.ENUM_ARCHIVE_MEMBERS]
    assert len(archive_tasks) == 1
    assert archive_tasks[0]["payload"]["archive_type"] == "tar"


@pytest.mark.unit
def test_enum_dir_tar_gz_file_emits_enum_archive_task(tmp_path: Path) -> None:
    """.tar.gz compound suffix is routed to ENUM_ARCHIVE_MEMBERS with archive_type='tar'."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "backup.tar.gz").write_bytes(b"placeholder")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    archive_tasks = [t for t in result.new_tasks if t["task_type"] == TaskType.ENUM_ARCHIVE_MEMBERS]
    assert len(archive_tasks) == 1
    assert archive_tasks[0]["payload"]["archive_type"] == "tar"


@pytest.mark.unit
def test_enum_dir_tgz_file_emits_enum_archive_task(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "backup.tgz").write_bytes(b"placeholder")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    archive_tasks = [t for t in result.new_tasks if t["task_type"] == TaskType.ENUM_ARCHIVE_MEMBERS]
    assert len(archive_tasks) == 1
    assert archive_tasks[0]["payload"]["archive_type"] == "tar"


@pytest.mark.unit
def test_enum_dir_zip_and_tar_gz_in_same_dir(tmp_path: Path) -> None:
    """Mixed directory: zip and tar.gz both routed correctly; zip detection unaffected."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "report.zip").write_bytes(b"z")
    (root / "backup.tar.gz").write_bytes(b"z")

    ctx = _make_ctx(tmp_path)
    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    archive_tasks = [t for t in result.new_tasks if t["task_type"] == TaskType.ENUM_ARCHIVE_MEMBERS]
    types = {Path(t["payload"]["archive_path"]).name: t["payload"]["archive_type"] for t in archive_tasks}
    assert types["report.zip"] == "zip"
    assert types["backup.tar.gz"] == "tar"


# ---------------------------------------------------------------------------
# Batches: extract_members, decompress_offset, batching, the traversal cap
# ---------------------------------------------------------------------------


def _build_archive(tmp_path: Path, archive_type: str, members: dict[str, bytes], name: str = "batch") -> Path:
    """Write members, in order, to a new archive of the given type."""
    import io
    import tarfile

    import py7zr

    if archive_type == "zip":
        path = tmp_path / f"{name}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for member, data in members.items():
                zf.writestr(member, data)
    elif archive_type == "7z":
        path = tmp_path / f"{name}.7z"
        with py7zr.SevenZipFile(path, "w") as szf:
            for member, data in members.items():
                szf.writestr(data, member)
    else:
        path = tmp_path / f"{name}.tar.gz"
        with tarfile.open(path, "w:gz") as tf:
            for member, data in members.items():
                info = tarfile.TarInfo(member)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return path


def _archive_handler(archive_type: str) -> Any:
    from piidigger.archivehandlers import get_handler

    return get_handler(archive_type)


_FORMATS = ["zip", "7z", "tar"]


@pytest.mark.unit
@pytest.mark.parametrize("archive_type", _FORMATS)
def test_extract_members_reports_requested_members_in_order_one_at_a_time(tmp_path: Path, archive_type: str) -> None:
    members = {f"dir/m{i}.txt": f"member {i}".encode() for i in range(5)}
    archive = _build_archive(tmp_path, archive_type, members)
    dest = tmp_path / "out"
    events: list[tuple[str, str]] = []

    def extracted(member: str, path: Path) -> None:
        on_disk = [p for p in dest.rglob("*") if p.is_file()]
        assert on_disk == [path], "only the member being handed over is on disk"
        assert path.read_bytes() == members[member]
        events.append(("done", member))
        path.unlink()

    _archive_handler(archive_type).extract_members(
        archive,
        ["dir/m1.txt", "dir/m3.txt"],
        dest,
        on_extracted=extracted,
        on_started=lambda member: events.append(("start", member)),
    )

    assert events == [("start", "dir/m1.txt"), ("done", "dir/m1.txt"), ("start", "dir/m3.txt"), ("done", "dir/m3.txt")]


@pytest.mark.unit
@pytest.mark.parametrize("archive_type", _FORMATS)
def test_extract_members_reports_progress_while_reading(tmp_path: Path, archive_type: str) -> None:
    archive = _build_archive(tmp_path, archive_type, {"a.txt": b"a" * 1000})
    ticks: list[int] = []

    _archive_handler(archive_type).extract_members(
        archive, ["a.txt"], tmp_path / "out", on_extracted=lambda m, p: None, on_progress=lambda: ticks.append(1)
    )

    assert ticks, "reading the archive must report progress"


@pytest.mark.unit
@pytest.mark.parametrize("archive_type", _FORMATS)
def test_extract_members_silently_skips_a_member_that_is_not_there(tmp_path: Path, archive_type: str) -> None:
    archive = _build_archive(tmp_path, archive_type, {"a.txt": b"a"})
    got: list[str] = []

    _archive_handler(archive_type).extract_members(
        archive, ["missing.txt", "a.txt"], tmp_path / "out", on_extracted=lambda m, p: got.append(m)
    )

    assert got == ["a.txt"]


@pytest.mark.unit
def test_tar_extract_members_refuses_a_traversal_member_and_carries_on(tmp_path: Path) -> None:
    """The data filter is a second line of defence behind enumeration's path check."""
    archive = _tar_fixture("traversal-member.tar")
    got: list[str] = []
    failed: list[str] = []

    _archive_handler("tar").extract_members(
        archive,
        ["../traversal.txt", "clean.txt"],
        tmp_path / "out",
        on_extracted=lambda m, p: got.append(m),
        on_failed=lambda m, reason: failed.append(m),
    )

    assert failed == ["../traversal.txt"]
    assert got == ["clean.txt"]
    assert not (tmp_path / "traversal.txt").exists()


@pytest.mark.unit
def test_7z_crc_failure_raises_and_leaves_no_open_file(tmp_path: Path) -> None:
    import shutil

    import py7zr

    from piidigger.exceptions import ArchiveReadError

    data = b"0123456789abcdef" * 64
    archive = tmp_path / "crc.7z"
    with py7zr.SevenZipFile(archive, "w", filters=[{"id": py7zr.FILTER_COPY}]) as szf:
        szf.writestr(data, "a.txt")
    raw = bytearray(archive.read_bytes())
    at = raw.index(data)
    raw[at] ^= 0xFF  # corrupt the stored (uncompressed) payload
    archive.write_bytes(bytes(raw))

    dest = tmp_path / "out"
    got: list[str] = []
    with pytest.raises(ArchiveReadError):
        _archive_handler("7z").extract_members(archive, ["a.txt"], dest, on_extracted=lambda m, p: got.append(m))

    assert got == [], "a member that failed its CRC is never handed over"
    shutil.rmtree(dest)  # fails on Windows if a handle was left open


@pytest.mark.unit
def test_decompress_offset_by_format(tmp_path: Path) -> None:
    import tarfile

    members = {"a.txt": b"a" * 1000, "b.txt": b"b" * 300, "c.txt": b"c" * 10}

    zip_members = _archive_handler("zip").list_members(_build_archive(tmp_path, "zip", members))
    assert [m.decompress_offset for m in zip_members] == [0, 0, 0], "zip reads any member directly"

    sz_members = _archive_handler("7z").list_members(_build_archive(tmp_path, "7z", members))
    assert [m.decompress_offset for m in sz_members] == [0, 1000, 1300], "solid: everything stored before it"

    tar_path = _build_archive(tmp_path, "tar", members)
    tar_offsets = [m.decompress_offset for m in _archive_handler("tar").list_members(tar_path)]
    with tarfile.open(tar_path) as tf:
        assert tar_offsets == [info.offset_data for info in tf.getmembers()]
    assert tar_offsets == sorted(tar_offsets)


@pytest.mark.unit
def test_batch_count() -> None:
    from piidigger.orchestration.worker._enum_archive import batch_count

    gib = 1024**3
    assert batch_count(0, 0, 8, gib) == 0
    assert batch_count(3, 100, 8, gib) == 3, "never more batches than members"
    assert batch_count(100, 100, 8, gib) == 8, "at least one per worker"
    assert batch_count(100, 10 * gib, 8, gib) == 10, "more when a batch would exceed the cap"


@pytest.mark.unit
def test_split_into_batches_keeps_order_and_balances_bytes() -> None:
    from piidigger.orchestration.worker._enum_archive import split_into_batches

    members = [(f"m{i}", 10) for i in range(8)]
    assert split_into_batches(members, 4) == [["m0", "m1"], ["m2", "m3"], ["m4", "m5"], ["m6", "m7"]]

    lopsided = [("big", 1000), ("s1", 1), ("s2", 1), ("s3", 1)]
    runs = split_into_batches(lopsided, 3)
    assert [m for run in runs for m in run] == ["big", "s1", "s2", "s3"], "contiguous, in order"
    assert all(runs), "no empty runs"

    empty = [(f"e{i}", 0) for i in range(4)]
    assert split_into_batches(empty, 2) == [["e0", "e1"], ["e2", "e3"]], "no bytes at all: split by count"


@pytest.mark.unit
def test_enum_archive_splits_members_into_one_batch_per_worker(tmp_path: Path) -> None:
    members = {f"m{i}.txt": b"x" * 100 for i in range(8)}
    archive = _build_archive(tmp_path, "tar", members)
    ctx = _make_ctx(tmp_path, n_workers=4)

    result = handle_enum_archive_members(_enum_archive_task(archive, archive_type="tar"), ctx, _logger())

    assert [t["items"] for t in result.new_tasks] == [
        ["m0.txt", "m1.txt"],
        ["m2.txt", "m3.txt"],
        ["m4.txt", "m5.txt"],
        ["m6.txt", "m7.txt"],
    ]
    assert all(t["task_type"] == TaskType.SCAN_ARCHIVE_MEMBERS for t in result.new_tasks)
    assert all(t["payload"]["depth"] == 1 for t in result.new_tasks)


@pytest.mark.unit
@pytest.mark.parametrize(("archive_type", "expected"), [("tar", []), ("zip", ["small.txt"])])
def test_enum_archive_skips_members_too_deep_to_reach(tmp_path: Path, archive_type: str, expected: list[str]) -> None:
    """A member behind 2 MB of skipped data is out of reach under a 1 MB cap, unless the format can seek."""
    members = {"huge.txt": b"h" * (2 * 1024 * 1024), "small.txt": b"s" * 10}
    archive = _build_archive(tmp_path, archive_type, members)
    ctx = _make_ctx(tmp_path, archives={"max_total_uncompressed_size_mb": 1, "max_member_uncompressed_size_mb": 1})

    result = handle_enum_archive_members(_enum_archive_task(archive, archive_type=archive_type), ctx, _logger())

    assert _member_paths(result) == expected
    assert result.counters.get("archive_members_skipped") == 2 - len(expected)


@pytest.mark.unit
def test_enum_archive_scans_a_repeated_path_once(tmp_path: Path) -> None:
    import io
    import tarfile

    archive = tmp_path / "dupe.tar"
    with tarfile.open(archive, "w") as tf:
        for data in (b"first", b"second"):
            info = tarfile.TarInfo("same.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    ctx = _make_ctx(tmp_path)

    result = handle_enum_archive_members(_enum_archive_task(archive, archive_type="tar"), ctx, _logger())

    assert _member_paths(result) == ["same.txt"]
    assert result.counters.get("archive_members_skipped") == 1


@pytest.mark.unit
@pytest.mark.parametrize("archive_type", _FORMATS)
def test_scan_batch_streams_each_member_and_leaves_nothing_on_disk(
    tmp_path: Path, archive_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from piidigger.orchestration.worker import _scan_archive_members as scan_mod

    members = {f"m{i}.txt": f"card {i}: 4111111111111111\n".encode() for i in range(3)}
    archive = _build_archive(tmp_path, archive_type, members)
    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "m0.txt", "m2.txt", archive_type=archive_type)
    task_temp = tmp_path / task.task_id

    real_scan = scan_mod._scan_member
    on_disk_during_scan: list[int] = []

    def counting_scan(*args: Any, **kwargs: Any) -> Any:
        on_disk_during_scan.append(sum(1 for p in task_temp.rglob("*") if p.is_file()))
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(scan_mod, "_scan_member", counting_scan)

    outcome = _scan(task, ctx)

    assert outcome.status == "ok"
    assert [(p.event, p.item) for p in outcome.progress if p.event != "alive"] == [
        ("item_started", "m0.txt"),
        ("item_done", "m0.txt"),
        ("item_started", "m2.txt"),
        ("item_done", "m2.txt"),
    ]
    assert [f["source_member_path"] for f in outcome.findings] == ["m0.txt", "m2.txt"]
    assert all(f["source_container_type"] == archive_type for f in outcome.findings)
    assert on_disk_during_scan == [1, 1], "one member on disk at a time"
    assert not [p for p in task_temp.rglob("*") if p.is_file()], "every member is deleted after its scan"
    assert outcome.result.findings == [], "findings are streamed, not held for the end"


@pytest.mark.unit
def test_scan_batch_reports_a_missing_member_as_failed(tmp_path: Path) -> None:
    archive = _build_archive(tmp_path, "zip", {"a.txt": b"hello"})
    ctx = _make_ctx(tmp_path)

    outcome = _scan(_scan_archive_task(archive, "a.txt", "gone.txt"), ctx)

    assert outcome.status == "ok"
    assert [p.item for p in outcome.done] == ["a.txt", "gone.txt"]
    assert outcome.done[1].counters == {"files_scanned": 1, "tasks_failed": 1}


@pytest.mark.unit
def test_tar_gz_batches_read_the_archive_a_bounded_number_of_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: K batches read about (K+1)/2 archives' worth, not one archive per member.

    One task per member used to read the whole archive for every member: 400
    times here.  The members are random bytes, so the archive is as large as
    its content and several times the reader's 1 MiB buffer.
    """
    import os

    from piidigger.archivehandlers import _progress_io
    from piidigger.orchestration.worker._enum_archive import split_into_batches

    members = {f"m{i:03d}.txt": os.urandom(20_000) for i in range(400)}
    archive = _build_archive(tmp_path, "tar", members)
    size = archive.stat().st_size

    read = [0]
    real_readinto = _progress_io._ProgressRaw.readinto

    def counting_readinto(self: Any, buffer: Any) -> int:
        count: int = real_readinto(self, buffer)
        read[0] += count
        return count

    monkeypatch.setattr(_progress_io._ProgressRaw, "readinto", counting_readinto)

    k = 4
    runs = split_into_batches([(name, len(data)) for name, data in members.items()], k)
    per_batch = []
    for run in runs:
        before = read[0]
        _archive_handler("tar").extract_members(archive, run, tmp_path / "out", on_extracted=lambda m, p: p.unlink())
        per_batch.append(read[0] - before)

    assert read[0] <= (k + 1) * size
    assert per_batch[0] < size / 2, "the first batch stops after its last member instead of reading to the end"


# ---------------------------------------------------------------------------
# Review fixes: member paths, per-member failures, duplicates, offsets
# ---------------------------------------------------------------------------


def _plain_tar(tmp_path: Path, members: dict[str, bytes], name: str = "plain") -> Path:
    import io
    import tarfile

    path = tmp_path / f"{name}.tar"
    with tarfile.open(path, "w") as tf:
        for member, data in members.items():
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.unit
@pytest.mark.parametrize("name", ["\\probe\\x.txt", "C:/probe/x.txt", "c:x.txt", "/abs/x.txt", "a/../../x.txt"])
def test_enum_archive_rejects_rooted_and_drive_member_names(tmp_path: Path, name: str) -> None:
    archive = _plain_tar(tmp_path, {name: b"secret", "ok.txt": b"fine"})
    ctx = _make_ctx(tmp_path)

    result = handle_enum_archive_members(_enum_archive_task(archive, archive_type="tar"), ctx, _logger())

    assert _member_paths(result) == ["ok.txt"]
    assert result.counters.get("archive_errors") == 1


@pytest.mark.unit
def test_tar_extract_members_hands_over_the_path_the_filter_wrote(tmp_path: Path) -> None:
    """A leading backslash is stripped by the data filter; the path handed over must match.

    Before the fix, dest_dir / "\\probe\\x.txt" named a file on the drive root
    on Windows, and the scan handler would have wiped whatever lived there.
    """
    archive = _plain_tar(tmp_path, {"\\probe\\x.txt": b"member data"})
    dest = tmp_path / "out"
    got: list[Path] = []

    _archive_handler("tar").extract_members(
        archive, ["\\probe\\x.txt"], dest, on_extracted=lambda m, p: got.append(p)
    )

    assert len(got) == 1
    assert got[0].resolve().is_relative_to(dest.resolve())
    assert got[0].read_bytes() == b"member data"


@pytest.mark.unit
def test_scan_batch_never_touches_a_path_outside_its_task_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth: whatever a handler hands over, only task_temp is scanned or deleted."""
    from piidigger.archivehandlers import _zip

    host_file = tmp_path / "host.txt"
    host_file.write_bytes(b"do not delete")

    def misbehaving_extract(archive_path: Path, members: Any, dest_dir: Path, **kwargs: Any) -> None:
        kwargs["on_extracted"]("a.txt", host_file)

    monkeypatch.setattr(_zip.handler, "extract_members", misbehaving_extract)
    archive = _build_archive(tmp_path, "zip", {"a.txt": b"x"})

    outcome = _scan(_scan_archive_task(archive, "a.txt"), _make_ctx(tmp_path))

    assert host_file.read_bytes() == b"do not delete"
    assert outcome.done[0].counters == {"files_scanned": 1, "tasks_failed": 1}


@pytest.mark.unit
def test_scan_batch_survives_a_member_it_cannot_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file handle left open (or an antivirus lock) must not end the batch or lose findings."""
    from piidigger.orchestration.worker import _scan_archive_members as scan_mod

    def locked(path: Path) -> None:
        raise PermissionError(13, "file is in use", str(path))

    monkeypatch.setattr(scan_mod, "secure_delete", locked)
    members = {"a.txt": b"card 4111111111111111\n", "b.txt": b"card 5555555555554444\n"}
    archive = _build_archive(tmp_path, "tar", members)
    ctx = _make_ctx(tmp_path, data_handlers=["pan"])

    outcome = _scan(_scan_archive_task(archive, "a.txt", "b.txt", archive_type="tar"), ctx)

    assert outcome.status == "ok"
    assert [f["source_member_path"] for f in outcome.findings] == ["a.txt", "b.txt"]
    assert "tasks_failed" not in outcome.counters


@pytest.mark.unit
@pytest.mark.parametrize("archive_type", _FORMATS)
def test_extract_members_fails_only_the_member_the_os_refuses(tmp_path: Path, archive_type: str) -> None:
    """A member whose file cannot be written (here, a directory is in the way) fails alone."""
    archive = _build_archive(tmp_path, archive_type, {"a.txt": b"blocked", "b.txt": b"fine"})
    dest = tmp_path / "out"
    (dest / "a.txt").mkdir(parents=True)
    got: list[str] = []
    failed: list[str] = []

    _archive_handler(archive_type).extract_members(
        archive,
        ["a.txt", "b.txt"],
        dest,
        on_extracted=lambda m, p: got.append(m),
        on_failed=lambda m, reason: failed.append(m),
    )

    assert failed == ["a.txt"]
    assert got == ["b.txt"]


@pytest.mark.unit
def test_zip_extracts_the_first_of_two_entries_with_the_same_name(tmp_path: Path) -> None:
    """Enumeration checks the first entry, so the first entry is the one extracted."""
    import warnings

    zp = tmp_path / "dupe.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile warns about the duplicate name
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("a.txt", b"first")
            zf.writestr("a.txt", b"second, unchecked")
    got: list[bytes] = []

    _archive_handler("zip").extract_members(
        zp, ["a.txt"], tmp_path / "out", on_extracted=lambda m, p: got.append(p.read_bytes())
    )

    assert got == [b"first"]


@pytest.mark.unit
def test_uncompressed_tar_reports_zero_decompress_offset(tmp_path: Path) -> None:
    members = {"a.txt": b"a" * 1000, "b.txt": b"b" * 10}

    offsets = [m.decompress_offset for m in _archive_handler("tar").list_members(_plain_tar(tmp_path, members))]

    assert offsets == [0, 0], "a plain tar seeks past member data, so reaching a member costs nothing"


@pytest.mark.unit
def test_enum_archive_does_not_skip_deep_members_of_an_uncompressed_tar(tmp_path: Path) -> None:
    members = {"huge.txt": b"h" * (2 * 1024 * 1024), "small.txt": b"s" * 10}
    archive = _plain_tar(tmp_path, members)
    ctx = _make_ctx(tmp_path, archives={"max_total_uncompressed_size_mb": 1, "max_member_uncompressed_size_mb": 1})

    result = handle_enum_archive_members(_enum_archive_task(archive, archive_type="tar"), ctx, _logger())

    assert _member_paths(result) == ["small.txt"]


# ---------------------------------------------------------------------------
# Member selection: the same include rule as files on disk
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ext", "mime", "include_exts", "include_mime", "scanned"),
    [
        (".txt", None, ["all"], ["all"], True),
        (".TXT", None, ["all"], ["all"], True),  # extensions match regardless of case
        (".PDF", None, [".pdf"], ["all"], True),
        (".txt", "text/plain", [".pdf"], ["all"], False),  # a recognised extension decides; MIME plays no part
        (".txt", "text/plain", [".pdf"], ["text/plain"], False),
        (".pdf", None, [".PDF"], ["all"], True),  # include_exts compared case-insensitively too
        ("", "application/pdf", [".pdf"], ["all"], True),  # unrecognised extension: MIME is the fallback
        ("", "application/pdf", ["all"], ["text/plain"], False),
        (".bak", "application/pdf", ["all"], ["application/pdf"], True),
        ("", None, ["all"], ["all"], False),  # no extension and no MIME: nothing to go on
        (".png", "image/png", ["all"], ["all"], False),  # no handler either way
    ],
)
def test_select_handler_rule(
    ext: str, mime: str | None, include_exts: list[str], include_mime: list[str], scanned: bool
) -> None:
    from piidigger.filehandlers import select_handler

    assert (select_handler(ext, mime, include_exts, include_mime) is not None) is scanned


@pytest.mark.unit
def test_select_handler_keeps_mime_first_handler_choice() -> None:
    """When a file is scanned, a known MIME type still picks the handler, as before."""
    from piidigger.filehandlers import get_handler_for, select_handler

    assert select_handler(".txt", "application/pdf", ["all"], ["all"]) is get_handler_for(".pdf", None)


@pytest.mark.unit
def test_enum_archive_accepts_upper_case_extensions(tmp_path: Path) -> None:
    """rbclark: REPORT.PDF and DATA.TXT from a Windows zip were silently dropped."""
    archive = _build_archive(tmp_path, "zip", {"REPORT.PDF": b"%PDF-1.4", "DATA.TXT": b"x", "Notes.Csv": b"a,b"})

    result = handle_enum_archive_members(_enum_archive_task(archive), _make_ctx(tmp_path), _logger())

    assert _member_paths(result) == ["REPORT.PDF", "DATA.TXT", "Notes.Csv"]


@pytest.mark.unit
def test_enum_archive_honours_include_exts(tmp_path: Path) -> None:
    """rbclark: with include_exts=['.pdf'], .txt and .csv members were still scanned."""
    archive = _build_archive(tmp_path, "zip", {"a.pdf": b"%PDF-1.4", "b.txt": b"x", "c.csv": b"a,b", "D.PDF": b"%PDF"})
    ctx = WorkerContext(
        config=Config(start_dirs=[], include_exts=[".pdf"]),
        task_queue=mp.Queue(),
        result_queue=queue.Queue(),  # type: ignore[arg-type]
        log_queue=_LOG_QUEUE,
        stop_event=mp.Event(),
        temp_base=tmp_path,
    )

    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert _member_paths(result) == ["a.pdf", "D.PDF"]
    assert result.counters.get("archive_members_skipped") == 2


@pytest.mark.unit
def test_enum_archive_logs_one_summary_of_unselected_types(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    members = {f"img{i}.png": b"x" for i in range(5)} | {"tool.exe": b"x", "readme": b"x", "a.txt": b"x"}
    archive = _build_archive(tmp_path, "zip", members)
    logger = logging.getLogger("tests.archives.summary")

    with caplog.at_level(logging.INFO, logger="tests.archives.summary"):
        result = handle_enum_archive_members(_enum_archive_task(archive), _make_ctx(tmp_path), logger)

    assert _member_paths(result) == ["a.txt"]
    summaries = [r.getMessage() for r in caplog.records if "not scanned, by type" in r.getMessage()]
    assert len(summaries) == 1, "one line per archive, not one per member"
    assert summaries[0].endswith(".png ×5, .exe ×1, (no extension) ×1")
    assert caplog.records[0].levelno == logging.INFO


@pytest.mark.unit
def test_scan_batch_reads_an_upper_case_member(tmp_path: Path) -> None:
    archive = _build_archive(tmp_path, "zip", {"DATA.TXT": b"card 4111111111111111\n"})
    ctx = _make_ctx(tmp_path, data_handlers=["pan"])

    outcome = _scan(_scan_archive_task(archive, "DATA.TXT"), ctx)

    assert [f["source_member_path"] for f in outcome.findings] == ["DATA.TXT"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "include_exts", "include_mime", "scanned"),
    [
        ("NOTES.CSV", ["all"], ["all"], True),  # text/csv has no handler, so only the extension can qualify it
        ("notes.txt", [".pdf"], ["all"], False),  # include_exts alone now narrows the scan
        ("notes.txt", [".pdf", ".docx"], [], False),  # the documented workaround still works
        ("report.pdf", [".pdf"], ["all"], True),
    ],
)
def test_enum_dir_follows_the_same_rule(
    tmp_path: Path, name: str, include_exts: list[str], include_mime: list[str], scanned: bool
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / name).write_bytes(b"%PDF-1.4\n" if name.lower().endswith(".pdf") else b"a,b\n1,2\n")
    ctx = WorkerContext(
        config=Config(start_dirs=[], include_exts=include_exts, include_mime=include_mime),
        task_queue=mp.Queue(),
        result_queue=queue.Queue(),  # type: ignore[arg-type]
        log_queue=_LOG_QUEUE,
        stop_event=mp.Event(),
        temp_base=tmp_path,
    )

    result = handle_enum_dir(_enum_dir_task(root), ctx, _logger())

    assert [Path(t["payload"]["file_path"]).name for t in result.new_tasks] == ([name] if scanned else [])
