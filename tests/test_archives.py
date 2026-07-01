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
  - handle_scan_archive_member (PII found, lineage fields, no-handler defensive path)
  - --no-archives CLI override logic
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import zipfile
from pathlib import Path
from typing import Any

import pytest

from piidigger.models.config import ArchiveConfig, Config
from piidigger.models.tasks import Task, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger
from piidigger.orchestration.secure_delete import secure_delete
from piidigger.orchestration.sources import FilesystemItem
from piidigger.orchestration.worker._enum_archive import handle_enum_archive_members
from piidigger.orchestration.worker._enum_dir import handle_enum_dir
from piidigger.orchestration.worker._scan_archive_member import handle_scan_archive_member
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
) -> WorkerContext:
    arc_cfg = ArchiveConfig(**(archives or {}))
    extra: dict[str, Any] = {}
    if data_handlers is not None:
        extra["data_handlers"] = data_handlers
    return WorkerContext(
        config=Config(start_dirs=[], archives=arc_cfg, **extra),
        task_queue=mp.Queue(),
        result_queue=mp.Queue(),
        log_queue=_LOG_QUEUE,
        stop_event=mp.Event(),
        temp_base=tmp_path,
    )


def _enum_archive_task(archive_path: Path, depth: int = 0, archive_type: str = "zip") -> Task:
    return Task(
        task_type=TaskType.ENUM_ARCHIVE_MEMBERS,
        payload={"archive_path": str(archive_path), "archive_type": archive_type, "depth": depth},
    )


def _scan_archive_task(
    archive_path: Path,
    member_path: str,
    ext: str,
    uncompressed_size: int = 64,
    depth: int = 1,
    archive_type: str = "zip",
) -> Task:
    return Task(
        task_type=TaskType.SCAN_ARCHIVE_MEMBER,
        payload={
            "archive_path": str(archive_path),
            "member_path": member_path,
            "archive_type": archive_type,
            "ext": ext,
            "mime": None,
            "uncompressed_size": uncompressed_size,
            "depth": depth,
        },
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
    assert t["task_type"] == TaskType.SCAN_ARCHIVE_MEMBER
    assert t["payload"]["member_path"] == "readme.txt"
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
    """5-member archive with max_members=3 → 3 tasks emitted, 2 skipped."""
    archive = _fixture("many-members.zip")
    ctx = _make_ctx(tmp_path, archives={"max_members": 3})
    result = handle_enum_archive_members(_enum_archive_task(archive), ctx, _logger())

    assert result.status == "ok"
    assert len(result.new_tasks) == 3
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
    assert result.new_tasks[0]["payload"]["member_path"] == "outer.txt"
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
# handle_scan_archive_member
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_archive_member_finds_pii(tmp_path: Path) -> None:
    archive = _fixture("simple-pii.zip")
    with zipfile.ZipFile(archive) as zf:
        size = zf.getinfo("readme.txt").file_size

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", ".txt", size)
    result = handle_scan_archive_member(task, ctx, _logger())

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
    with zipfile.ZipFile(zp) as zf:
        size = zf.getinfo("notes.txt").file_size
    task = _scan_archive_task(zp, "notes.txt", ".txt", size)
    result = handle_scan_archive_member(task, ctx, _logger())

    assert result.status == "ok"
    assert result.findings == []
    assert result.counters.get("files_scanned") == 1


@pytest.mark.unit
def test_scan_archive_member_result_lineage(tmp_path: Path) -> None:
    """ResultRecord lineage fields are populated correctly."""
    archive = _fixture("simple-pii.zip")
    with zipfile.ZipFile(archive) as zf:
        size = zf.getinfo("readme.txt").file_size

    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", ".txt", size, depth=1)
    result = handle_scan_archive_member(task, ctx, _logger())

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
    task = _scan_archive_task(zp, "f.txt", ".txt", size)
    result = handle_scan_archive_member(task, ctx, _logger())

    assert result.counters.get("bytes_scanned") == size


@pytest.mark.unit
def test_scan_archive_member_unknown_handler_returns_error(tmp_path: Path) -> None:
    """Defensive: if a member somehow reaches scan with no handler, status=error."""
    zp = tmp_path / "noh.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("file.xyz999", "data")

    ctx = _make_ctx(tmp_path)
    task = _scan_archive_task(zp, "file.xyz999", ".xyz999", 4)
    result = handle_scan_archive_member(task, ctx, _logger())

    assert result.status == "error"
    assert result.counters.get("files_scanned") == 1


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
def test_7z_handler_extract_member(tmp_path: Path) -> None:
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
    result = szhandler.extract_member(archive, "fox.txt", dest_dir)
    assert result.exists()
    assert result.read_bytes() == payload
    assert result.parent == dest_dir


@pytest.mark.unit
def test_zip_handler_extract_member(tmp_path: Path) -> None:
    from piidigger.archivehandlers._zip import handler as ziphandler

    payload = b"zip extract content"
    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("data.txt", payload)

    dest_dir = tmp_path / "out"
    result = ziphandler.extract_member(zp, "data.txt", dest_dir)
    assert result.exists()
    assert result.read_bytes() == payload
    assert result.parent == dest_dir


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
    assert t["task_type"] == TaskType.SCAN_ARCHIVE_MEMBER
    assert t["payload"]["member_path"] == "readme.txt"
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
    """5-member archive with max_members=3 → 3 tasks emitted, 2 skipped."""
    archive = _7z_fixture("many-members.7z")
    ctx = _make_ctx(tmp_path, archives={"max_members": 3})
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="7z"), ctx, _logger()
    )

    assert result.status == "ok"
    assert len(result.new_tasks) == 3
    assert result.counters.get("archive_members_skipped", 0) == 2


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
# handle_scan_archive_member — 7z
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
    task = _scan_archive_task(archive, "readme.txt", ".txt", len(content), archive_type="7z")
    result = handle_scan_archive_member(task, ctx, _logger())

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
    task = _scan_archive_task(archive, "readme.txt", ".txt", len(content), depth=1, archive_type="7z")
    result = handle_scan_archive_member(task, ctx, _logger())

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
def test_tar_handler_extract_member(tmp_path: Path) -> None:
    from piidigger.archivehandlers._tar import handler

    archive = _tar_fixture("simple-pii.tar")
    extracted = handler.extract_member(archive, "readme.txt", tmp_path)

    assert extracted.exists()
    assert b"4111111111111111" in extracted.read_bytes()


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
    assert result.new_tasks[0]["payload"]["member_path"] == "readme.txt"
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
    assert len(result.new_tasks) == 3
    assert result.counters.get("archive_members_skipped", 0) == 2


@pytest.mark.unit
def test_enum_archive_tar_traversal_member_rejected(tmp_path: Path) -> None:
    archive = _tar_fixture("traversal-member.tar")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    member_paths = [t["payload"]["member_path"] for t in result.new_tasks]
    assert not any(".." in p for p in member_paths)


@pytest.mark.unit
def test_enum_archive_tar_symlink_member_not_scanned(tmp_path: Path) -> None:
    """Symlink members excluded by list_members() produce no SCAN_ARCHIVE_MEMBER tasks."""
    archive = _tar_fixture("symlink-member.tar")
    ctx = _make_ctx(tmp_path)
    result = handle_enum_archive_members(
        _enum_archive_task(archive, archive_type="tar"), ctx, _logger()
    )

    assert result.status == "ok"
    member_paths = [t["payload"]["member_path"] for t in result.new_tasks]
    assert "link-to-readme.txt" not in member_paths


# ---------------------------------------------------------------------------
# handle_scan_archive_member — tar
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_archive_member_tar_finds_pii(tmp_path: Path) -> None:
    archive = _tar_fixture("simple-pii.tar")
    content_size = len(b"card number: 4111111111111111\n")
    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", ".txt", content_size, archive_type="tar")
    result = handle_scan_archive_member(task, ctx, _logger())

    assert result.status == "ok"
    assert len(result.findings) >= 1
    assert result.findings[0]["handler"] == "pan"


@pytest.mark.unit
def test_scan_archive_member_tar_lineage(tmp_path: Path) -> None:
    """source_container_type is 'tar' regardless of compression flavor."""
    archive = _tar_fixture("simple-pii.tar.gz")
    content_size = len(b"card number: 4111111111111111\n")
    ctx = _make_ctx(tmp_path, data_handlers=["pan"])
    task = _scan_archive_task(archive, "readme.txt", ".txt", content_size, depth=1, archive_type="tar")
    result = handle_scan_archive_member(task, ctx, _logger())

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
