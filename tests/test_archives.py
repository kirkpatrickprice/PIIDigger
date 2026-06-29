"""Phase 5 tests: ZIP archive support.

Covers:
  - ArchiveConfig model (defaults, TOML round-trip, unknown-key rejection)
  - ArchiveMemberItem (protocol compliance, open_bytes, open_stream, materialize)
  - FilesystemItem.open_bytes() still returns None
  - secure_delete() utility
  - handle_enum_dir archive routing (zip → ENUM_ARCHIVE_MEMBERS; archives disabled)
  - handle_enum_archive_members safety checks (8 scenarios)
  - handle_scan_archive_member (PII found, lineage fields, no-handler defensive path)
  - --no-archives CLI override logic
"""
from __future__ import annotations

import multiprocessing as mp
import zipfile
from pathlib import Path

import pytest

from piidigger.models.config import ArchiveConfig, Config
from piidigger.models.tasks import Task, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.logging_setup import build_worker_logger
from piidigger.orchestration.secure_delete import secure_delete
from piidigger.orchestration.sources import ArchiveMemberItem, FilesystemItem
from piidigger.orchestration.worker._enum_archive import handle_enum_archive_members
from piidigger.orchestration.worker._enum_dir import handle_enum_dir
from piidigger.orchestration.worker._scan_archive_member import handle_scan_archive_member
from piidigger.protocols import ScannableItem

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LOG_QUEUE: mp.Queue = mp.Queue()  # type: ignore[type-arg]
_FIXTURES = Path("testdata/zip")


def _logger() -> object:
    return build_worker_logger(_LOG_QUEUE, "test-phase5")


def _fixture(name: str) -> Path:
    p = _FIXTURES / name
    if not p.exists():
        pytest.skip(f"fixture {p} not found — run testdata/zip/create_fixtures.py")
    return p


def _make_ctx(
    tmp_path: Path,
    *,
    archives: dict | None = None,
    data_handlers: list[str] | None = None,
) -> WorkerContext:
    arc_cfg = ArchiveConfig(**(archives or {}))
    extra: dict = {}
    if data_handlers is not None:
        extra["data_handlers"] = data_handlers
    return WorkerContext(
        config=Config(start_dirs=[], archives=arc_cfg, **extra),  # type: ignore[arg-type]
        task_queue=mp.Queue(),
        result_queue=mp.Queue(),
        log_queue=_LOG_QUEUE,
        stop_event=mp.Event(),
        temp_base=tmp_path,
    )


def _enum_archive_task(archive_path: Path, depth: int = 0) -> Task:
    return Task(
        task_type=TaskType.ENUM_ARCHIVE_MEMBERS,
        payload={"archive_path": str(archive_path), "depth": depth},
    )


def _scan_archive_task(
    archive_path: Path,
    member_path: str,
    ext: str,
    uncompressed_size: int = 64,
    depth: int = 1,
) -> Task:
    return Task(
        task_type=TaskType.SCAN_ARCHIVE_MEMBER,
        payload={
            "archive_path": str(archive_path),
            "member_path": member_path,
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
    assert cfg.formats == ["zip"]
    assert cfg.max_depth == 1
    assert cfg.max_members == 10_000
    assert cfg.max_member_uncompressed_size_mb == 64
    assert cfg.max_total_uncompressed_size_mb == 1024


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
    assert config.archives.max_total_uncompressed_size_mb == 1024  # default preserved


@pytest.mark.unit
def test_archive_config_unknown_key_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ArchiveConfig(typo_field="bad")  # type: ignore[call-arg]


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
# ArchiveMemberItem — protocol compliance and I/O methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_archive_member_satisfies_protocol(tmp_path: Path) -> None:
    zp = tmp_path / "a.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("hello.txt", "hello")
    item = ArchiveMemberItem(
        archive_path=zp,
        member_path="hello.txt",
        uncompressed_size=5,
        mime=None,
        depth=1,
        task_temp=tmp_path,
    )
    assert isinstance(item, ScannableItem)


@pytest.mark.unit
def test_archive_member_display_path(tmp_path: Path) -> None:
    zp = tmp_path / "arch.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("sub/file.txt", "x")
    item = ArchiveMemberItem(
        archive_path=zp,
        member_path="sub/file.txt",
        uncompressed_size=1,
        mime=None,
        depth=1,
        task_temp=tmp_path,
    )
    assert item.display_path == f"{zp}::sub/file.txt"


@pytest.mark.unit
def test_archive_member_open_bytes_returns_correct_content(tmp_path: Path) -> None:
    payload = b"hello from archive"
    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("data.bin", payload)
    item = ArchiveMemberItem(
        archive_path=zp,
        member_path="data.bin",
        uncompressed_size=len(payload),
        mime=None,
        depth=1,
        task_temp=tmp_path,
    )
    assert item.open_bytes() == payload


@pytest.mark.unit
def test_archive_member_open_bytes_returns_bytes_not_none(tmp_path: Path) -> None:
    """ArchiveMemberItem.open_bytes() always returns bytes, never None."""
    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("x.txt", "x")
    item = ArchiveMemberItem(
        archive_path=zp, member_path="x.txt",
        uncompressed_size=1, mime=None, depth=1, task_temp=tmp_path,
    )
    result = item.open_bytes()
    assert result is not None
    assert isinstance(result, bytes)


@pytest.mark.unit
def test_archive_member_open_stream_reads_correct_content(tmp_path: Path) -> None:
    payload = b"stream content"
    zp = tmp_path / "s.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("member.txt", payload)
    item = ArchiveMemberItem(
        archive_path=zp, member_path="member.txt",
        uncompressed_size=len(payload), mime=None, depth=1, task_temp=tmp_path,
    )
    stream = item.open_stream()
    try:
        assert stream.read() == payload
    finally:
        stream.close()


@pytest.mark.unit
def test_archive_member_materialize_creates_file_in_task_temp(tmp_path: Path) -> None:
    payload = b"materialized bytes"
    zp = tmp_path / "m.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("report.txt", payload)
    task_temp = tmp_path / "task123"
    item = ArchiveMemberItem(
        archive_path=zp, member_path="report.txt",
        uncompressed_size=len(payload), mime=None, depth=1, task_temp=task_temp,
    )
    dest = item.materialize()
    assert dest.exists()
    assert dest.read_bytes() == payload
    assert dest.parent == task_temp


@pytest.mark.unit
def test_archive_member_ext_from_member_path(tmp_path: Path) -> None:
    zp = tmp_path / "e.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("report.xlsx", b"")
    item = ArchiveMemberItem(
        archive_path=zp, member_path="report.xlsx",
        uncompressed_size=0, mime=None, depth=1, task_temp=tmp_path,
    )
    assert item.ext == ".xlsx"


@pytest.mark.unit
def test_archive_member_size_returns_uncompressed_size(tmp_path: Path) -> None:
    zp = tmp_path / "z.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("f.txt", "abc")
    item = ArchiveMemberItem(
        archive_path=zp, member_path="f.txt",
        uncompressed_size=999, mime=None, depth=2, task_temp=tmp_path,
    )
    assert item.size == 999
    assert item.depth == 2


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
