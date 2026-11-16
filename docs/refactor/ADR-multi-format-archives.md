# ADR — Multi-Format Archive Support (7z + future tar.*)

**Branch**: `refactor`  
**Status**: Implemented — including this document's own "revision 3" steps (§9). Verified directly against the code on 2026-07-06: `ArchiveHandler` has only `extract_member()` (no `open_bytes`/`open_stream`), `ArchiveMemberItem` is deleted in favor of `FilesystemItem` taking optional `archive_path`/`member_path` kwargs, and all §9 steps previously marked `⏳ Pending` are done. Tar was added later using this same shape (see [TAR_HANDLING_PLAN.md](./TAR_HANDLING_PLAN.md)). For the current design description, see [docs/architecture/archives/archive-handling.md](../architecture/archives/archive-handling.md); this ADR remains useful as the historical record of *why* the temp-dir approach was chosen over ZIP's original in-memory streaming.  
**Last Updated**: 2026-07-06  
**Reference**: [ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md), [PHASE5_PLAN.md](./PHASE5_PLAN.md)

---

## Design Revision Note

The original approved design (revision 1) specified `open_bytes()` and `open_stream()` on the `ArchiveHandler` protocol, following `ZIP_HANDLING_PLAN.md` design principle 6: *"In-memory archive extraction is preferred."* ZIP supports true streaming via `zipfile.ZipFile.open()`; the design assumed 7z could do the same.

During implementation, **py7zr 1.1.3 has no per-member in-memory read API.** Its only extraction path writes to a filesystem directory (`SevenZipFile.extract(path=<dir>)`). Implementing `open_bytes()` for 7z required `tempfile.TemporaryDirectory()` — an unmanaged, unsecure temp location not governed by `secure_delete()`.

This revision supersedes the streaming design. All archive formats use a **unified temp-dir extraction path**. This means:

- One code path for all current and future formats (simpler, more maintainable)
- No format-specific branching in `ArchiveMemberItem`
- All temp files land in the managed `task_temp` directory already used by `materialize()`
- `secure_delete()` is called on each extracted file immediately after it is read
- ZIP's in-memory streaming advantage is abandoned in favour of architectural consistency

Design principle 6 from `ZIP_HANDLING_PLAN.md` is superseded by this ADR. Principles 7 and 8 (isolated temp paths, secure deletion) are strengthened and now apply to all code paths.

---

## 1. Context

Phase 5 shipped ZIP archive support. The architecture is clean — `ENUM_ARCHIVE_MEMBERS` → `SCAN_ARCHIVE_MEMBER` task fan-out, an `ArchiveMemberItem` source abstraction, and configurable safety limits — but the implementation is ZIP-specific at four coupling points:

| Location | ZIP-specific code |
|---|---|
| `_enum_archive.py` | Hardcodes `ZipFile` / `BadZipFile`; imports `zipfile` at module level |
| `sources.py:ArchiveMemberItem` | `open_bytes`, `open_stream`, and `materialize` all call `zipfile.ZipFile` directly |
| `_scan_archive_member.py` | `source_container_type="zip"` hardcoded in `ResultRecord` construction |
| `models/payloads.py` | Payload models carry no `archive_type` field — format is implicit |

Adding 7z support (via `py7zr`) requires opening all four points. This ADR documents the chosen approach.

---

## 2. Chosen Design — `archivehandlers/` Package

Archive formats are treated as **just another handler category**, consistent with how `filehandlers/`, `datahandlers/`, and `outputhandlers/` are structured. A top-level `piidigger/archivehandlers/` package holds one module per format; a registry maps `archive_type` strings to handler instances. The orchestration layer (`_enum_archive.py`, `ArchiveMemberItem`) becomes format-agnostic — it holds no library imports, only a registry lookup.

### Why not a private dispatch layer inside `orchestration/`?

The alternative was adding `_list_zip()` / `_list_7z()` helper functions inside `_enum_archive.py` and branching in `ArchiveMemberItem.open_bytes()`. That approach was rejected because:

- **Format logic would be split across two modules.** Listing code lives in `_enum_archive.py`; I/O code lives in `sources.py`. Adding any new format requires touching both.
- **It does not match the codebase pattern.** Every other extensible category in this project uses a handler package with a registry. An ad-hoc dispatch function inside a worker module is an outlier.
- **Open/closed violation.** Each new format modifies existing modules rather than extending them with a new file.

The `archivehandlers/` package extends the existing pattern cleanly. Archive handlers are tightly coupled to the orchestration worker layer, but the same is true of `filehandlers/` and `datahandlers/` — they are only consumed by workers — yet they live at the top level. Archives are "just another filetype" from the project's structural perspective.

---

## 3. Shared Types and Where They Live

`MemberInfo` and `ArchiveReadError` are used by both `archivehandlers/` modules and the orchestration layer that consumes them. Placing them inside `archivehandlers/__init__.py` would create an import cycle (the handler modules would import from `archivehandlers`, which triggers the handler imports). They are therefore placed in purpose-fit modules at the package root.

### 3.1 `piidigger/exceptions.py` — new file, `ArchiveReadError`

A dedicated exceptions module is the conventional Python home for custom exception classes (`requests`, `boto3`, SQLAlchemy all follow this pattern). It has no imports from within `piidigger/`, so it can never participate in a cycle. This also establishes a clean location for any future custom exceptions (`ConfigError`, etc.).

```python
class ArchiveReadError(Exception):
    """Raised by ArchiveHandler implementations when an archive cannot be opened or listed.

    Each format module catches its own library-specific exceptions
    (BadZipFile, py7zr exceptions, tarfile.TarError, …) and re-raises as
    ArchiveReadError so callers stay format-agnostic.
    """
```

### 3.2 `piidigger/models/archive.py` — new file, `MemberInfo` as Pydantic model

`MemberInfo` is a structured data-transfer object produced by `list_members()` and consumed by the safety-check loop in `handle_enum_archive_members()`. This fits the same pattern as every other model in `models/` — a validated, frozen Pydantic model.

```python
from piidigger.models.base import PiiDiggerModel
from pydantic import Field, ConfigDict

class MemberInfo(PiiDiggerModel):
    model_config = ConfigDict(frozen=True)

    name: str
    uncompressed_size: int = Field(ge=0)
    compressed_size: int = Field(ge=0)
    is_dir: bool
    is_encrypted: bool
```

---

## 4. Detailed Design

### 4.1 `protocols.py` — add `ArchiveHandler`

The `ArchiveHandler` protocol has two methods: `list_members()` for enumeration and `extract_member()` for I/O. The original `open_bytes()` and `open_stream()` methods are not on the protocol — extraction is always disk-based, and the `ArchiveMemberItem` layer owns the decision of what to do with the extracted file.

```python
from piidigger.models.archive import MemberInfo

class ArchiveHandler(Protocol):
    """Implemented by each format module in piidigger/archivehandlers/.

    list_members() is called during ENUM_ARCHIVE_MEMBERS to inspect the
    archive without extracting any content to disk.

    extract_member() is called during SCAN_ARCHIVE_MEMBER to extract one
    member to a caller-provided directory.  The handler writes the member
    content to dest_dir / Path(member_path).name (flat — no subdirectory
    structure) and returns that path.  The caller owns the lifecycle of
    the extracted file.
    """

    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        """Return all entries (dirs included) from the archive.

        Raises ArchiveReadError on any open or parse failure.
        No content is extracted to disk during this call.
        """
        ...

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        """Extract one member to dest_dir and return the file path.

        The caller provides dest_dir (a managed temp directory).  The
        handler creates dest_dir if it does not exist.  The extracted
        file is written to dest_dir / Path(member_path).name (flat, no
        subdirectory nesting).  Raises ArchiveReadError on failure.
        """
        ...
```

### 4.2 `piidigger/archivehandlers/__init__.py` — registry only

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from piidigger.archivehandlers import _7z, _zip

if TYPE_CHECKING:
    from piidigger.protocols import ArchiveHandler

HANDLER_REGISTRY: dict[str, ArchiveHandler] = {}

for _mod in (_zip, _7z):
    HANDLER_REGISTRY[_mod.ARCHIVE_TYPE] = _mod.handler


def get_handler(archive_type: str) -> ArchiveHandler | None:
    return HANDLER_REGISTRY.get(archive_type)
```

### 4.3 `piidigger/archivehandlers/_zip.py`

`list_members()` reads the central directory without extracting. `extract_member()` reads the member bytes in-memory via `ZipFile.read()` and writes them to the flat destination — this keeps the ZIP implementation simple and avoids relying on `ZipFile.extract()` which preserves subdirectory structure.

```python
from __future__ import annotations

from pathlib import Path
from zipfile import BadZipFile, ZipFile

from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "zip"


class ZipArchiveHandler:
    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        try:
            with ZipFile(archive_path, "r") as zf:
                return [
                    MemberInfo(
                        name=info.filename,
                        uncompressed_size=info.file_size,
                        compressed_size=info.compress_size,
                        is_dir=info.filename.endswith("/"),
                        is_encrypted=bool(info.flag_bits & 0x1),
                    )
                    for info in zf.infolist()
                ]
        except (BadZipFile, OSError) as exc:
            raise ArchiveReadError(str(exc)) from exc

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / Path(member_path).name
            with ZipFile(archive_path, "r") as zf:
                dest.write_bytes(zf.read(member_path))
            return dest
        except (BadZipFile, OSError, KeyError) as exc:
            raise ArchiveReadError(str(exc)) from exc


handler = ZipArchiveHandler()
```

### 4.4 `piidigger/archivehandlers/_7z.py`

`extract_member()` calls `py7zr.SevenZipFile.extract()` into `dest_dir`. Because py7zr preserves the internal path structure of the member, the extracted file may land at `dest_dir/subdir/file.txt`. The method resolves the full path using `dest_dir / member_path` and renames it flat to `dest_dir / Path(member_path).name`. Any empty intermediate subdirectory created by py7zr is removed.

```python
from __future__ import annotations

from pathlib import Path

from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "7z"


class SevenZArchiveHandler:
    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        try:
            import py7zr  # lazy: only loaded when a .7z file is encountered
            with py7zr.SevenZipFile(archive_path, mode="r") as szf:
                all_encrypted = szf.needs_password()
                raw = szf.list()
            return [
                MemberInfo(
                    name=info.filename,
                    uncompressed_size=info.uncompressed or 0,
                    compressed_size=info.compressed or 0,
                    is_dir=info.is_directory,
                    is_encrypted=all_encrypted,
                )
                for info in raw
            ]
        except ImportError as exc:
            raise ArchiveReadError(f"py7zr is required for .7z archives: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — py7zr exception hierarchy varies by version
            raise ArchiveReadError(str(exc)) from exc

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        try:
            import py7zr

            dest_dir.mkdir(parents=True, exist_ok=True)
            with py7zr.SevenZipFile(archive_path, mode="r") as szf:
                szf.extract(path=str(dest_dir), targets=[member_path])

            # py7zr preserves internal path structure; flatten to dest_dir
            extracted = dest_dir / member_path
            if not extracted.exists():
                raise ArchiveReadError(f"member {member_path!r} not found after extraction from {archive_path}")
            flat_dest = dest_dir / Path(member_path).name
            if extracted != flat_dest:
                extracted.rename(flat_dest)
                # Remove any empty intermediate directory py7zr created
                parent = extracted.parent
                if parent != dest_dir:
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
            return flat_dest
        except ArchiveReadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArchiveReadError(str(exc)) from exc


handler = SevenZArchiveHandler()
```

**7z encryption note**: unlike ZIP (per-member flag bit), 7z encryption is archive-level. `needs_password()` returns `True` when the header is encrypted; all members inherit the same flag. An encrypted 7z archive results in every member being skipped by the `is_encrypted` safety check — correct and safe behaviour.

**`uncompressed` may be `None`** for directory entries in 7z. The `or 0` guard is required; the Pydantic `Field(ge=0)` on `MemberInfo.uncompressed_size` validates the result.

**`compressed` may be 0 per member in solid archives** (the block is compressed as a unit). The bomb-ratio check `compressed_size > 0 and uncompressed_size > compressed_size * 1000` already skips the check when `compressed_size == 0`, so solid archives pass through safely with no code change.

### 4.5 `models/payloads.py` — add `archive_type`

```python
class EnumArchiveMembersPayload(PiiDiggerModel):
    archive_path: Path
    archive_type: str = "zip"   # default preserves backward compat with in-flight tasks
    depth: int = Field(default=0, ge=0, le=3)

class ScanArchiveMemberPayload(PiiDiggerModel):
    archive_path: Path
    member_path: str
    archive_type: str = "zip"   # default preserves backward compat with in-flight tasks
    ext: str
    mime: str | None
    uncompressed_size: int = Field(ge=0)
    depth: int = Field(default=1, ge=1, le=4)
```

### 4.6 `models/config.py` — default formats use `"all"`

Consistent with `include_exts`, `include_mime`, and `data_handlers`, the `"all"` sentinel expands at runtime to every format registered in `HANDLER_REGISTRY`. This means adding a new handler package automatically makes that format available to users who haven't customised their config.

```python
# ArchiveConfig.formats — "all" expands to every key in archivehandlers.HANDLER_REGISTRY
formats: list[str] = Field(default_factory=lambda: ["all"])
```

```python
# generate_toml_template():
'formats = ["all"]',
```

The expansion logic lives in `_is_archive_format()` in `_enum_dir.py` (see §4.7).

### 4.7 `orchestration/worker/_enum_dir.py` — two changes

**Change 1**: Add `archive_type` to the emitted payload (1 line):

```python
"payload": {
    "archive_path": str(entry),
    "archive_type": ext.lstrip(".").lower(),   # "zip", "7z", etc.
    "depth": 0,
},
```

**Change 2**: Update `_is_archive_format()` to handle the `"all"` sentinel:

```python
def _is_archive_format(ext: str, config: Config) -> bool:
    if not config.archives.enabled:
        return False
    from piidigger.archivehandlers import HANDLER_REGISTRY   # lazy: avoid top-level cycle
    ext_bare = ext.lstrip(".").lower()
    if "all" in config.archives.formats:
        return ext_bare in HANDLER_REGISTRY
    return ext_bare in {fmt.lower().lstrip(".") for fmt in config.archives.formats}
```

### 4.8 `orchestration/worker/_enum_archive.py` — format-agnostic

Replace the `try: with ZipFile(...)` block with a registry lookup. `list_members()` reads archive metadata only — no content is extracted to disk during enumeration.

```python
from piidigger.archivehandlers import HANDLER_REGISTRY, get_handler
from piidigger.exceptions import ArchiveReadError

# Build nested-archive skip set from all registered formats
_NESTED_ARCHIVE_EXTS: frozenset[str] = frozenset(f".{k}" for k in HANDLER_REGISTRY)

handler = get_handler(payload.archive_type)
if handler is None:
    logger.warning("no archive handler registered for type %r", payload.archive_type)
    return TaskResult(...status="error"...)

try:
    member_list = handler.list_members(archive_path)
except ArchiveReadError as exc:
    logger.warning("cannot enumerate archive %s: %s", archive_path, exc)
    return TaskResult(...status="error"...)
```

The **safety-check loop is unchanged in logic**. The only field-name changes are:

| Old (ZipInfo) | New (MemberInfo) |
|---|---|
| `info.filename.endswith("/")` | `member.is_dir` |
| `info.filename` | `member.name` |
| `info.file_size` | `member.uncompressed_size` |
| `info.compress_size` | `member.compressed_size` |
| `info.flag_bits & 0x1` | `member.is_encrypted` |

Additional changes:
- Nested-archive skip set derived from `HANDLER_REGISTRY` keys rather than hardcoded `{".zip"}`.
- Include `"archive_type": payload.archive_type` in each emitted `SCAN_ARCHIVE_MEMBER` payload so the type propagates to the scan task.
- Remove the `from zipfile import BadZipFile, ZipFile` import.

### 4.9 Temp Directory Security Model and `FilesystemItem` Archive Context

Once a member is extracted to `task_temp`, it is a regular file on disk. Wrapping it in an `ArchiveMemberItem` — a parallel `ScannableItem` implementation that duplicates the I/O interface — adds complexity without benefit. The cleaner design passes the extracted file to file handlers as a `FilesystemItem`, exactly as on-disk files are handled today.

**`FilesystemItem` extended with optional archive context:**

Two keyword-only parameters are added to `FilesystemItem.__init__()`. When set, `display_path` returns the `archive::member` form used in logs; when absent, behaviour is unchanged.

```python
class FilesystemItem:
    def __init__(
        self,
        path: Path,
        mime: str | None = None,
        *,
        archive_path: Path | None = None,   # set when item came from an archive
        member_path: str | None = None,
    ) -> None:
        self._path = path
        self._mime = mime
        self._archive_path = archive_path
        self._member_path = member_path

    @property
    def display_path(self) -> str:
        if self._archive_path is not None and self._member_path is not None:
            return f"{self._archive_path}::{self._member_path}"
        return str(self._path)

    # open_stream(), open_bytes(), materialize(), ext, size, depth — all unchanged
```

**Extraction lifecycle:**

```
SCAN_ARCHIVE_MEMBER task starts
  └─ handle_scan_archive_member():
       1. get_handler(archive_type) → ArchiveHandler
       2. handler.extract_member(archive_path, member_path, task_temp) → Path
       3. FilesystemItem(extracted_path, mime,
                         archive_path=archive_path, member_path=member_path)
       4. file_handler.read(item)  ← same call as for any on-disk file
       5. aggregate matches → TaskResult

task handler returns TaskResult
  └─ worker loop _cleanup_temp_workspace(task_id) [try/finally, always runs]
       └─ secure_delete() on every file in task_temp
       └─ rmdir(task_temp)
```

**Key properties:**

| Property | How it is achieved |
|---|---|
| Extraction only at scan time | `extract_member()` is called once, inside `handle_scan_archive_member`, immediately before scanning |
| Managed location | `task_temp = ctx.temp_base / task.task_id`; never `tempfile.TemporaryDirectory()` |
| Flat layout | Handler writes to `dest_dir / Path(member_path).name`; `_cleanup_temp_workspace()` iterates one level |
| No duplicate I/O abstraction | `FilesystemItem` is the only `ScannableItem` implementation; `ArchiveMemberItem` is deleted |
| Archive context in logs | `display_path` returns `archive.zip::member.txt` when archive context is set |
| Bounded exposure window | Members bounded by `max_member_uncompressed_size_mb`; tasks bounded by `task_timeout_seconds` (default 30s) |
| Cleanup at task end | `_cleanup_temp_workspace()` in `_loop.py` calls `secure_delete()` on every file in `task_temp` after every task |
| Crash safety | Worker restart enqueues a replacement SCAN_ARCHIVE_MEMBER task; `task_temp` is cleaned up on next start |

### 4.10 `orchestration/worker/_scan_archive_member.py` — simplified

The handler replaces the `ArchiveMemberItem` construction and its format-dispatch plumbing with a direct extract-then-wrap sequence:

```python
from piidigger.archivehandlers import get_handler
from piidigger.exceptions import ArchiveReadError
from piidigger.orchestration.sources import FilesystemItem

# resolve handler
archive_handler = get_handler(payload.archive_type)
if archive_handler is None:
    return TaskResult(...status="error", error_message=f"no handler for {payload.archive_type!r}")

# extract member
task_temp = ctx.temp_base / task.task_id
task_temp.mkdir(exist_ok=True)
extracted_path = archive_handler.extract_member(
    payload.archive_path, payload.member_path, task_temp
)

# wrap as a regular file, preserving archive display context
item = FilesystemItem(
    extracted_path,
    mime=payload.mime,
    archive_path=payload.archive_path,
    member_path=payload.member_path,
)

# scan through file handler chain — identical to handle_scan_file from here
```

`source_container_type=payload.archive_type` in `ResultRecord` construction is unchanged.

### 4.11 `pyproject.toml` — two changes

Add `py7zr` to dependencies:
```toml
"py7zr>=1.1.3,<1.2",
```

Add `archivehandlers` to the mypy strict overrides block:
```toml
[[tool.mypy.overrides]]
module = [
    "piidigger.orchestration.*",
    "piidigger.archivehandlers.*",
]
strict = true
enable_error_code = ["no-untyped-call"]
```

### 4.12 User Documentation

The user guide must include a **Security Considerations** entry disclosing temp directory usage:

> **Archive member extraction**: When scanning archive files (ZIP, 7z, and others), PIIDigger extracts individual members to a temporary directory during processing. The temporary directory is located under the system temp path (`%TEMP%` on Windows, `/tmp` on Linux/macOS) in a subdirectory specific to the current scan run. Each extracted file is securely overwritten (two-pass: zeros then random bytes) and deleted at the end of the task that processed it — typically within the task timeout window (default: 30 seconds). No extracted content persists beyond the enclosing scan task.
>
> **SSD caveat**: Secure overwriting cannot guarantee physical data erasure on solid-state storage due to wear-levelling. This limitation is inherent to SSD hardware and cannot be addressed in software. The overwrite-before-delete approach is still applied as a best effort.

---

## 5. Import Graph (no cycles)

```
exceptions.py           ← no piidigger imports
models/base.py          ← no piidigger imports
models/archive.py       ← models/base.py
protocols.py            ← models/archive.py
archivehandlers/
  __init__.py           ← protocols.py (TYPE_CHECKING only)
  _zip.py               ← exceptions.py, models/archive.py
  _7z.py                ← exceptions.py, models/archive.py
orchestration/
  secure_delete.py      ← no piidigger imports
  sources.py            ← no archive-related imports (FilesystemItem only)
  worker/_enum_dir.py   ← archivehandlers (lazy, inside _is_archive_format)
  worker/_enum_archive.py ← archivehandlers, exceptions.py
  worker/_scan_archive_member.py ← archivehandlers, exceptions.py, sources (FilesystemItem)
```

`sources.py` has no archive-related imports — `FilesystemItem`'s optional archive context fields are plain `Path | None` and `str | None`. The lazy import inside `_is_archive_format()` avoids any top-level cycle risk at module load time.

---

## 6. Files Changed

| File | Nature of change |
|---|---|
| `pyproject.toml` | Add `py7zr>=1.1.3,<1.2`; add `piidigger.archivehandlers.*` to mypy strict overrides |
| `src/piidigger/exceptions.py` | **New** — `ArchiveReadError` |
| `src/piidigger/models/archive.py` | **New** — `MemberInfo` Pydantic model |
| `src/piidigger/protocols.py` | Add `ArchiveHandler` protocol with `list_members()` + `extract_member()` |
| `src/piidigger/archivehandlers/__init__.py` | **New** — `HANDLER_REGISTRY`, `get_handler()`, trigger imports |
| `src/piidigger/archivehandlers/_zip.py` | **New** — `ZipArchiveHandler` with `list_members()` + `extract_member()` |
| `src/piidigger/archivehandlers/_7z.py` | **New** — `SevenZArchiveHandler` with `list_members()` + `extract_member()` |
| `src/piidigger/models/payloads.py` | Add `archive_type: str = "zip"` to two payload models |
| `src/piidigger/models/config.py` | `ArchiveConfig.formats` default → `["all"]`; update TOML template |
| `src/piidigger/orchestration/worker/_enum_dir.py` | Add `archive_type` to payload dict; update `_is_archive_format()` for `"all"` |
| `src/piidigger/orchestration/worker/_enum_archive.py` | Replace `ZipFile` block with handler lookup; `ZipInfo` field renames; derive nested-archive set from registry; propagate `archive_type` |
| `src/piidigger/orchestration/sources.py` | **Delete** `ArchiveMemberItem`; extend `FilesystemItem.__init__()` with `archive_path`/`member_path` kwargs; override `display_path` to return `archive::member` when set |
| `src/piidigger/orchestration/worker/_scan_archive_member.py` | Replace `ArchiveMemberItem` construction with direct `get_handler()` → `extract_member()` → `FilesystemItem(..., archive_path=..., member_path=...)`; add `archivehandlers` and `exceptions` imports |
| `testdata/7z/create_fixtures.py` | **New** — fixture creation script |
| `testdata/7z/*.7z` | **New** — test fixture files |
| `tests/test_archives.py` | Update `test_archive_config_defaults`; add 7z and `extract_member` tests |
| User guide | Add Security Considerations entry for archive temp extraction |

No changes to: coordinator, worker loop, progress display, output handlers, data handlers, file handlers, CLI, or any other module.

### What does NOT change

- The `ENUM_ARCHIVE_MEMBERS` → `SCAN_ARCHIVE_MEMBER` task contract.
- All 8 ZIP safety checks and their log levels.
- The `ScannableItem` protocol — no new methods, no new required properties.
- All file handlers — they receive a `ScannableItem` and are unaware of its origin.
- The `_cleanup_temp_workspace()` logic in `_loop.py`.

---

## 7. New Tests Required

### 7.1 One existing assertion to update

`test_archive_config_defaults`: change `assert cfg.formats == ["zip"]` → `assert cfg.formats == ["all"]`.

### 7.2 New and revised tests (all in `tests/test_archives.py`)

**`FilesystemItem` archive context (new):**

| Test | What it verifies |
|---|---|
| `test_filesystem_item_display_path_with_archive_context` | When `archive_path`/`member_path` are set, `display_path` returns `archive::member` form |
| `test_filesystem_item_display_path_without_archive_context` | Default `display_path` still returns plain file path (no regression) |

**`extract_member()` handler unit tests (new — replaces `open_bytes`/`open_stream` handler tests):**

| Test | What it verifies |
|---|---|
| `test_zip_handler_extract_member_returns_correct_content` | `ZipArchiveHandler.extract_member()` writes correct bytes, returns correct flat path |
| `test_zip_handler_extract_member_corrupt_raises` | Corrupt archive → `ArchiveReadError` |
| `test_7z_handler_extract_member_returns_correct_content` | `SevenZArchiveHandler.extract_member()` writes correct bytes, returns correct flat path |
| `test_7z_handler_list_members_uses_attribute_access` | `FileInfo.filename`, `.uncompressed`, `.compressed`, `.is_directory` (not dict) |

**`ArchiveMemberItem` tests — deleted:**

All tests in the `ArchiveMemberItem — protocol compliance and I/O methods` section are removed. Coverage of the extraction → scan path is provided by the `handle_scan_archive_member` integration tests, which remain unchanged.

**Existing `handle_enum_archive_members` / `handle_scan_archive_member` tests** remain valid and unchanged. `archive_type="zip"` default means all existing ZIP fixture tests continue to work.

**7z enumeration and scan tests (already present):**

| Test | What it verifies |
|---|---|
| `test_enum_archive_7z_simple_pii_emits_scan_task` | `list_members()` path; correct task emitted |
| `test_enum_archive_7z_encrypted_skipped` | Encrypted 7z → all members `is_encrypted=True` → all skipped |
| `test_enum_archive_7z_corrupt_returns_error` | `ArchiveReadError` on list → `status="error"` result |
| `test_enum_archive_7z_oversize_member_skipped` | 100 MB member exceeds 64 MB default limit |
| `test_enum_archive_7z_member_count_limit` | 5-member archive, `max_members=3` → 3 tasks, 2 skipped |
| `test_enum_archive_unknown_type_returns_error` | `archive_type="rar"` with no handler → `status="error"` |
| `test_scan_archive_member_7z_finds_pii` | End-to-end: extract → scan → finding with correct PAN |
| `test_scan_archive_member_7z_lineage` | `source_container_type == "7z"` in finding |
| `test_enum_dir_7z_file_emits_enum_archive_task` | `.7z` file routed to `ENUM_ARCHIVE_MEMBERS` with `archive_type="7z"` |
| `test_enum_dir_archive_type_in_payload` | Both `.zip` and `.7z` in same dir → correct `archive_type` per file |

### 7.3 Test fixtures in `testdata/7z/`

Script: `testdata/7z/create_fixtures.py` (follows pattern of `testdata/zip/create_fixtures.py`).

| Fixture | Purpose |
|---|---|
| `simple-pii.7z` | Text member with known PAN — happy path |
| `corrupt.7z` | Non-7z bytes — error path |
| `encrypted.7z` | Header-encrypted archive — skip path |
| `many-members.7z` | 5 members — member count limit test |
| `oversize-member.7z` | 100 MB of zeros (LZMA2 → ~15 KB on disk) — size limit test |

---

## 8. Closed Decisions

**`archive_type` representation — plain strings**  
Plain strings (`"zip"`, `"7z"`) match the way `formats` is configured in TOML and avoid an additional import at every call site. `get_handler()` returning `None` is the validation point, producing a clear warning log.

**Default formats — `["all"]`**  
Since this is a 2.0 release with no existing users, there is no upgrade behaviour concern. `["all"]` follows the established pattern used by `include_exts`, `include_mime`, and `data_handlers`, and means newly registered format handlers are automatically active without config changes.

**py7zr version constraint — `>=1.1.3,<1.2`**  
Current stable version is 1.1.3. The `<1.2` upper bound guards against potential breaking changes within the 1.x series while remaining permissive about patch releases.

**`MemberInfo` placement — `models/archive.py` as a Pydantic model**  
Consistent with every other structured data-transfer object in the project. Pydantic validation (`Field(ge=0)`) adds a useful sanity check on handler output.

**`ArchiveReadError` placement — `piidigger/exceptions.py`**  
Dedicated exceptions module at the package root. No internal imports, so no cycle risk. Establishes a clean location for future custom exceptions.

**mypy strict for `archivehandlers/`**  
Add `piidigger.archivehandlers.*` to the `[[tool.mypy.overrides]]` strict block in `pyproject.toml`. New code starts typed.

**Unified temp-dir extraction for all archive formats** *(revision 2)*  
The `ArchiveHandler` protocol exposes `extract_member()` rather than `open_bytes()` and `open_stream()`. All extraction — for all current and future archive formats — writes to the caller-provided `task_temp` directory. The rationale:

- py7zr 1.1.x has no per-member in-memory read API; extraction to disk is unavoidable for 7z.
- Maintaining two code paths (in-memory for ZIP, temp-file for 7z and others) increases complexity without proportional benefit.
- A single extraction path is easier to audit, test, and extend to future formats (tar, RAR, etc.).
- Security properties are uniform: all extraction uses the managed `task_temp` directory and `secure_delete()` at task end.

This decision supersedes `ZIP_HANDLING_PLAN.md` design principle 6 (*"In-memory archive extraction is preferred"*). Principles 7 and 8 (*managed temp paths*; *secure deletion*) are now the primary constraints and apply without exception.

**`FilesystemItem` as the sole `ScannableItem` implementation** *(revision 3)*  
`ArchiveMemberItem` is deleted. After extraction, the worker passes a `FilesystemItem` to file handlers — the same path taken by every on-disk file. This means:

- File handlers require zero changes; they call `open_stream()`, `open_bytes()`, or `materialize()` on a `FilesystemItem` regardless of whether the file came from an archive or the filesystem.
- The `ScannableItem` protocol is unchanged.
- `sources.py` carries no archive-related imports.
- Archive context (`archive_path`, `member_path`) is threaded through as optional kwargs on `FilesystemItem.__init__()`. The `display_path` property uses them when set so that any log message referencing the item still shows `archive.zip::member.txt` rather than the temp file path.
- The format-dispatch plumbing that lived in `ArchiveMemberItem._get_handler()` moves to `handle_scan_archive_member`, where it belongs — the worker handler owns format resolution, not the source item.

**PST/OST future handling — separate `mailstorehandlers/` package**  
Outlook PST and OST files use a different addressing model (items have store paths, not file paths), different safety checks, and may require different task types. A future `mailstorehandlers/` package following the same registration pattern is the right home; no architecture changes are needed now.

---

## 9. Implementation Sequence

All 17 steps are **complete**, verified directly against the code on 2026-07-06 (not just checked off from memory — see [docs/architecture/archives/archive-handling.md](../architecture/archives/archive-handling.md) for the resulting design).

| # | Step | Status |
|---|---|---|
| 1 | `pyproject.toml` — add `py7zr`; update mypy overrides | ✅ Done |
| 2 | `exceptions.py` — `ArchiveReadError` | ✅ Done |
| 3 | `models/archive.py` — `MemberInfo` Pydantic model | ✅ Done |
| 4 | `protocols.py` — revise `ArchiveHandler`: remove `open_bytes`/`open_stream`, add `extract_member` | ✅ Done |
| 5 | `archivehandlers/__init__.py` — registry skeleton | ✅ Done |
| 6 | `archivehandlers/_zip.py` — replace `open_bytes`/`open_stream` with `extract_member` | ✅ Done |
| 7 | `archivehandlers/_7z.py` — replace `open_bytes`/`open_stream` (and `tempfile` usage) with `extract_member` | ✅ Done |
| 8 | `models/payloads.py` — add `archive_type` to both payload models | ✅ Done |
| 9 | `models/config.py` — update `formats` default and TOML template | ✅ Done |
| 10 | `orchestration/sources.py` — delete `ArchiveMemberItem`; extend `FilesystemItem` with optional `archive_path`/`member_path` kwargs and updated `display_path` | ✅ Done |
| 11 | `orchestration/worker/_enum_dir.py` — already done; no change | ✅ Done |
| 12 | `orchestration/worker/_enum_archive.py` — already done; no change | ✅ Done |
| 13 | `orchestration/worker/_scan_archive_member.py` — replace `ArchiveMemberItem` with direct `get_handler()` → `extract_member()` → `FilesystemItem(... archive_path=..., member_path=...)` | ✅ Done |
| 14 | `testdata/7z/create_fixtures.py` — already created and run | ✅ Done |
| 15 | `tests/test_archives.py` — delete `ArchiveMemberItem` tests; add `FilesystemItem` archive context tests; replace `open_bytes`/`open_stream` handler tests with `extract_member` tests | ✅ Done |
| 16 | `uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest tests/ -v` | ✅ Done — clean, 354 passed / 1 skipped (Windows CTRL-C test, expected) |
| 17 | User documentation — Security Considerations entry for archive temp extraction | ✅ Done — see `docs/user-guides/archive-handling.md` |
