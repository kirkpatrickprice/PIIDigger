# ADR — Multi-Format Archive Support (7z + future tar.*)

**Branch**: `refactor`  
**Status**: Approved — ready for implementation  
**Last Updated**: 2026-06-30  
**Reference**: [ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md), [PHASE5_PLAN.md](./PHASE5_PLAN.md)

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
    """Format-neutral descriptor for one entry in an archive.

    Produced by ArchiveHandler.list_members(); consumed by
    handle_enum_archive_members() to apply safety checks.
    Directory entries are included (is_dir=True) so the member-count
    stop logic counts non-directory members correctly.
    """
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

`MemberInfo` is imported from `models.archive`. `ArchiveReadError` is not referenced in the method signatures (exceptions are documented, not typed in Protocol signatures).

```python
from piidigger.models.archive import MemberInfo

class ArchiveHandler(Protocol):
    """Implemented by each format module in piidigger/archivehandlers/."""

    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        """Open the archive and return all entries (dirs included) as MemberInfo.

        Raises ArchiveReadError on any open or parse failure.
        Includes directory entries so the member-count stop logic in
        handle_enum_archive_members counts correctly.
        """
        ...

    def open_bytes(self, archive_path: Path, member_path: str) -> bytes:
        """Return the full uncompressed content of one member as bytes.

        Raises ArchiveReadError if the member cannot be read.
        """
        ...

    def open_stream(self, archive_path: Path, member_path: str) -> IO[bytes]:
        """Return a readable binary stream for one member.

        Implementations that support true streaming (e.g. ZIP) return a live
        handle.  Others may return io.BytesIO(self.open_bytes(...)).
        """
        ...
```

### 4.2 `piidigger/archivehandlers/__init__.py` — registry only

`MemberInfo` and `ArchiveReadError` live elsewhere; this module owns only the registry and the trigger imports.

```python
from __future__ import annotations

from piidigger.protocols import ArchiveHandler


HANDLER_REGISTRY: dict[str, ArchiveHandler] = {}


def get_handler(archive_type: str) -> ArchiveHandler | None:
    return HANDLER_REGISTRY.get(archive_type)


# Trigger handler self-registration — same pattern as filehandlers/__init__.py.
from piidigger.archivehandlers import _zip, _7z  # noqa: E402, F401
```

### 4.3 `piidigger/archivehandlers/_zip.py`

```python
from __future__ import annotations

from pathlib import Path
from typing import IO
from zipfile import BadZipFile, ZipFile

from piidigger.archivehandlers import HANDLER_REGISTRY
from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo


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

    def open_bytes(self, archive_path: Path, member_path: str) -> bytes:
        try:
            with ZipFile(archive_path, "r") as zf:
                return zf.read(member_path)
        except (BadZipFile, OSError) as exc:
            raise ArchiveReadError(str(exc)) from exc

    def open_stream(self, archive_path: Path, member_path: str) -> IO[bytes]:
        # ZipFile.open() returns a ZipExtFile that closes its parent ZipFile
        # when the stream is closed — true streaming, no in-memory copy.
        try:
            zf = ZipFile(archive_path, "r")
            return zf.open(member_path)
        except (BadZipFile, OSError) as exc:
            raise ArchiveReadError(str(exc)) from exc


HANDLER_REGISTRY["zip"] = ZipArchiveHandler()
```

### 4.4 `piidigger/archivehandlers/_7z.py`

```python
from __future__ import annotations

import io
from pathlib import Path
from typing import IO

from piidigger.archivehandlers import HANDLER_REGISTRY
from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo


class SevenZArchiveHandler:
    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        try:
            import py7zr  # lazy: only loaded when a .7z file is encountered
            with py7zr.SevenZipFile(archive_path, mode="r") as szf:
                all_encrypted = szf.needs_password()
                raw = szf.list()
            return [
                MemberInfo(
                    name=info["filename"],
                    uncompressed_size=info["uncompressed"] or 0,
                    compressed_size=info["compressed"] or 0,
                    is_dir=info["is_directory"],
                    is_encrypted=all_encrypted,
                )
                for info in raw
            ]
        except ImportError as exc:
            raise ArchiveReadError(f"py7zr is required for .7z archives: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — py7zr exception hierarchy varies by version
            raise ArchiveReadError(str(exc)) from exc

    def open_bytes(self, archive_path: Path, member_path: str) -> bytes:
        try:
            import py7zr
            with py7zr.SevenZipFile(archive_path, mode="r") as szf:
                result = szf.read(targets=[member_path])
            if result is None or member_path not in result:
                raise ArchiveReadError(f"member {member_path!r} not found in {archive_path}")
            return result[member_path].read()
        except ArchiveReadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArchiveReadError(str(exc)) from exc

    def open_stream(self, archive_path: Path, member_path: str) -> IO[bytes]:
        # py7zr has no true per-member streaming API; BytesIO wrapping open_bytes
        # is correct. Members are bounded by max_member_uncompressed_size_mb.
        return io.BytesIO(self.open_bytes(archive_path, member_path))


HANDLER_REGISTRY["7z"] = SevenZArchiveHandler()
```

**7z encryption note**: unlike ZIP (per-member flag bit), 7z encryption is archive-level. `needs_password()` returns `True` when the header is encrypted; all members inherit the same flag. An encrypted 7z archive results in every member being skipped by the `is_encrypted` safety check — correct and safe behaviour.

**`uncompressed` may be `None`** for directory entries in 7z. The `or 0` guard is required; the Pydantic `Field(ge=0)` on `MemberInfo.uncompressed_size` validates the result.

**`compressed` may be 0 per member in solid archives** (the block is compressed as a unit). The bomb-ratio check `compressed_size > 0 and uncompressed_size > compressed_size * 1000` already skips the check when `compressed_size == 0`, so solid archives pass through safely with no code change.

**`py7zr.SevenZipFile.read()` return type** is `Optional[Dict[str, IO[bytes]]]` per py7zr's type annotations. The `if result is None or member_path not in result` guard is required for correctness and mypy strict.

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

Replace the `try: with ZipFile(...)` block with a registry lookup:

```python
from piidigger.archivehandlers import get_handler
from piidigger.exceptions import ArchiveReadError

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
- Expand the nested-archive skip set from `{".zip"}` to `{".zip", ".7z"}`.
- Include `"archive_type": payload.archive_type` in each emitted `SCAN_ARCHIVE_MEMBER` payload so the type propagates to the scan task.
- Remove the `from zipfile import BadZipFile, ZipFile` import.

### 4.9 `orchestration/sources.py:ArchiveMemberItem` — format-agnostic

Add `archive_type: str = "zip"` as a constructor parameter and `self._archive_type` field. Replace `open_bytes` and `open_stream`:

```python
def open_bytes(self) -> bytes:
    from piidigger.archivehandlers import get_handler
    handler = get_handler(self._archive_type)
    if handler is None:
        raise ValueError(f"no archive handler for type {self._archive_type!r}")
    return handler.open_bytes(self._archive_path, self._member_path)

def open_stream(self) -> IO[bytes]:
    from piidigger.archivehandlers import get_handler
    handler = get_handler(self._archive_type)
    if handler is None:
        raise ValueError(f"no archive handler for type {self._archive_type!r}")
    return handler.open_stream(self._archive_path, self._member_path)
```

`materialize()` requires **no change** — it already delegates entirely to `open_bytes()` and automatically gains multi-format support without modification.

The `import zipfile` at the top of `sources.py` is removed entirely.

### 4.10 `orchestration/worker/_scan_archive_member.py` — two one-liners

1. Pass `archive_type=payload.archive_type` to the `ArchiveMemberItem` constructor.
2. Change `source_container_type="zip"` → `source_container_type=payload.archive_type`.

### 4.11 `pyproject.toml` — two changes

Add `py7zr` to dependencies:
```toml
"py7zr>=1.1.3,<1.2",
```

Add `archivehandlers` to the mypy strict overrides block:
```toml
[[tool.mypy.overrides]]
module = "piidigger.orchestration.*"
strict = true
enable_error_code = ["no-untyped-call"]
```
becomes:
```toml
[[tool.mypy.overrides]]
module = [
    "piidigger.orchestration.*",
    "piidigger.archivehandlers.*",
]
strict = true
enable_error_code = ["no-untyped-call"]
```

---

## 5. Import Graph (no cycles)

```
exceptions.py           ← no piidigger imports
models/base.py          ← no piidigger imports
models/archive.py       ← models/base.py
protocols.py            ← models/archive.py
archivehandlers/
  __init__.py           ← protocols.py
  _zip.py               ← archivehandlers (HANDLER_REGISTRY), exceptions.py, models/archive.py
  _7z.py                ← archivehandlers (HANDLER_REGISTRY), exceptions.py, models/archive.py
orchestration/
  sources.py            ← archivehandlers (lazy, inside methods)
  worker/_enum_dir.py   ← archivehandlers (lazy, inside _is_archive_format)
  worker/_enum_archive.py ← archivehandlers, exceptions.py
  worker/_scan_archive_member.py ← (no new imports)
```

The lazy imports inside `_is_archive_format()` and `ArchiveMemberItem` methods avoid any top-level cycle risk at module load time.

---

## 6. Files Changed

| File | Nature of change |
|---|---|
| `pyproject.toml` | Add `py7zr>=1.1.3,<1.2`; add `piidigger.archivehandlers.*` to mypy strict overrides |
| `src/piidigger/exceptions.py` | **New** — `ArchiveReadError` |
| `src/piidigger/models/archive.py` | **New** — `MemberInfo` Pydantic model |
| `src/piidigger/protocols.py` | Add `ArchiveHandler` protocol; import `MemberInfo` from `models.archive` |
| `src/piidigger/archivehandlers/__init__.py` | **New** — `HANDLER_REGISTRY`, `get_handler()`, trigger imports |
| `src/piidigger/archivehandlers/_zip.py` | **New** — `ZipArchiveHandler`; self-registers |
| `src/piidigger/archivehandlers/_7z.py` | **New** — `SevenZArchiveHandler`; self-registers |
| `src/piidigger/models/payloads.py` | Add `archive_type: str = "zip"` to two payload models |
| `src/piidigger/models/config.py` | `ArchiveConfig.formats` default → `["all"]`; update TOML template |
| `src/piidigger/orchestration/worker/_enum_dir.py` | Add `archive_type` to payload dict; update `_is_archive_format()` for `"all"` |
| `src/piidigger/orchestration/worker/_enum_archive.py` | Replace `ZipFile` block with handler lookup; `ZipInfo` field renames; expand nested-archive set; propagate `archive_type` |
| `src/piidigger/orchestration/sources.py` | Add `archive_type` param; dispatch `open_bytes`/`open_stream` to handler; remove `zipfile` import |
| `src/piidigger/orchestration/worker/_scan_archive_member.py` | Pass `archive_type` to item constructor; use for `source_container_type` |
| `tests/test_archives.py` | Update `test_archive_config_defaults`; add 7z tests (§7) |

No changes to: coordinator, worker loop, progress display, output handlers, data handlers, file handlers, CLI, or any other module.

### What does NOT change

- The `ENUM_ARCHIVE_MEMBERS` → `SCAN_ARCHIVE_MEMBER` task contract.
- All 8 ZIP safety checks and their log levels.
- The `ArchiveMemberItem` protocol surface (same public methods, same return types).
- All existing ZIP tests — the `"zip"` default in payload models and the `ArchiveMemberItem` constructor means no existing test helper or fixture needs modification.

---

## 7. New Tests Required

### 7.1 One existing assertion to update

`test_archive_config_defaults`: change `assert cfg.formats == ["zip"]` → `assert cfg.formats == ["all"]`.

### 7.2 New 7z unit tests (all in `tests/test_archives.py`)

| Test | What it verifies |
|---|---|
| `test_enum_dir_7z_file_emits_enum_archive_task` | `.7z` file routed to `ENUM_ARCHIVE_MEMBERS` with `archive_type="7z"` in payload |
| `test_enum_archive_7z_simple_pii_emits_scan_task` | `SevenZArchiveHandler.list_members()` path produces correct `SCAN_ARCHIVE_MEMBER` tasks |
| `test_enum_archive_7z_encrypted_archive_skipped` | Encrypted 7z → all members `is_encrypted=True` → all skipped |
| `test_enum_archive_7z_corrupt_returns_error` | Bad 7z → `ArchiveReadError` → `status="error"` result |
| `test_scan_archive_member_7z_finds_pii` | End-to-end: 7z member read via `ArchiveMemberItem.open_bytes()` |
| `test_scan_archive_member_7z_result_lineage` | `source_container_type == "7z"` in finding |
| `test_archive_member_7z_open_bytes` | `ArchiveMemberItem(archive_type="7z").open_bytes()` returns correct content |
| `test_archive_member_7z_open_stream` | Same via `open_stream()` (BytesIO path) |
| `test_archive_member_7z_materialize` | `materialize()` extracts correct content with no change in `ArchiveMemberItem` |

### 7.3 New test fixtures in `testdata/7z/`

Create `testdata/7z/create_fixtures.py` following the pattern of `testdata/zip/create_fixtures.py`.

| Fixture | Purpose |
|---|---|
| `simple-pii.7z` | Text member with known PAN — happy path |
| `corrupt.7z` | Truncated/invalid header — error path |
| `encrypted.7z` | Header-encrypted archive — skip path |
| `many-members.7z` | ≥5 members — member count limit test |
| `oversize-member.7z` | One member with declared uncompressed size > 64 MB |

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

---

## 9. Implementation Sequence

1. `pyproject.toml` — add `py7zr`; update mypy overrides; run `uv sync --extra dev`
2. `exceptions.py` — `ArchiveReadError`
3. `models/archive.py` — `MemberInfo` Pydantic model
4. `protocols.py` — add `ArchiveHandler` protocol
5. `archivehandlers/__init__.py` — registry skeleton (`HANDLER_REGISTRY`, `get_handler()`)
6. `archivehandlers/_zip.py` — `ZipArchiveHandler`; moves ZIP logic out of `_enum_archive.py` and `sources.py`
7. `archivehandlers/_7z.py` — `SevenZArchiveHandler`
8. `archivehandlers/__init__.py` — add trigger imports for `_zip` and `_7z`
9. `models/payloads.py` — add `archive_type` to both payload models
10. `models/config.py` — update `formats` default and TOML template
11. `orchestration/worker/_enum_dir.py` — add `archive_type` to payload dict; update `_is_archive_format()`
12. `orchestration/worker/_enum_archive.py` — replace `ZipFile` block with handler lookup; field renames; expand nested-archive set
13. `orchestration/sources.py` — update `ArchiveMemberItem`; remove `zipfile` import
14. `orchestration/worker/_scan_archive_member.py` — two one-line fixes
15. `testdata/7z/create_fixtures.py` — new fixture script; run it
16. `tests/test_archives.py` — update one assertion; add 7z tests
17. `uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest tests/ -v`
