# Tar Archive Support — Implementation Plan

**Branch**: `refactor`
**Status**: Pre-implementation — design proposed, pending review
**Last Updated**: 2026-06-30
**Reference**: [ADR-multi-format-archives.md](./ADR-multi-format-archives.md) (authoritative — this plan extends it, does not override it), [ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md), [PHASE5_PLAN.md](./PHASE5_PLAN.md)

---

## 1. Purpose

ZIP and 7z are implemented and tested under the `archivehandlers/` registry pattern locked in by `ADR-multi-format-archives.md`. This document plans a third format module, `_tar.py`, covering `.tar` and its compressed variants (gzip, bzip2, xz/lzma). It follows the same structure as `PHASE5_PLAN.md`: close open design questions, call out constraints the ADR didn't anticipate, and produce an implementation checklist.

Tar is mostly a drop-in fit for the existing `ArchiveHandler` protocol — `list_members()` / `extract_member()` map directly onto stdlib `tarfile` calls, and **no new third-party dependency is required** (`tarfile` wraps `gzip`, `bz2`, and `lzma`, all stdlib). One assumption baked into the existing pipeline does *not* hold for tar, though, and is the main subject of this plan: **archive-type detection assumes a single-suffix extension** (`.zip`, `.7z`). Tarballs commonly use compound extensions (`.tar.gz`, `.tar.bz2`) that `Path.suffix` cannot detect. §3 below proposes the fix.

---

## 2. Closed Decisions

### Decision 1 — One registry entry (`"tar"`), not one per compression

**Decision: a single `TarArchiveHandler` registered under `ARCHIVE_TYPE = "tar"` handles every compression flavor.**

`tarfile.open(path, mode="r:*")` transparently detects gzip/bzip2/lzma compression (or none) by sniffing the stream — the handler never needs to know up front which flavor it's looking at. This means one handler module, one registry key, and one `archive_type` value flows through `EnumArchiveMembersPayload` / `ScanArchiveMemberPayload` regardless of whether the file is `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`, or `.txz`.

Rejected alternative: separate registry entries (`"tar.gz"`, `"tar.bz2"`, …). This would quadruple the module count for zero behavioral difference — `list_members()` and `extract_member()` would be byte-for-byte identical across them, differing only in the `tarfile.open()` mode string, which `"r:*"` already resolves automatically. It would also require users to enumerate every flavor in `[archives].formats` individually instead of writing `"tar"` once.

Consequence: `config.archives.formats = ["tar"]` (or `"all"`) enables every compression flavor together. Flavors are not independently toggleable, matching how `zip` and `7z` are already single on/off switches.

### Decision 2 — Extension detection must move beyond `Path.suffix`; use the `HANDLES` per-module pattern

**Decision: each archive handler module declares a `HANDLES = {"ext": [...]}` dict listing every extension it recognizes, including compound-suffix aliases. `archivehandlers/__init__.py` builds a detection registry from those declarations, using longest-first `endswith` matching. No MIME detection is added for archives.**

This is the one place where tar genuinely doesn't fit the pattern the ADR established for zip/7z, and it requires a real (small) code change in `_enum_dir.py`, not just a new handler file. See §3 for the full design — this is not a "Closed Decision" in the sense of being a minor footnote; it's load-bearing for whether tar archives are even routed to `ENUM_ARCHIVE_MEMBERS` at all.

`HANDLES = {"ext": [...]}` mirrors the exact pattern used by every file handler (`plaintext.py`, `pdf.py`, `docx.py`, etc.) and `filehandlers/__init__.py`. Archive handler modules self-declare what they recognize; the registry is built mechanically from those declarations. This was previously deferred as a "future refinement" in an earlier draft of this section; it is adopted now because tar is precisely the case that motivates it.

**MIME detection was considered and rejected** for archive routing. The compressed-variant MIME types (`application/gzip`, `application/x-bzip2`, `application/x-xz`) identify the outer compression wrapper, not whether a tar archive is inside — every plain `.gz` log file or `.bz2` database dump shares the same MIME type as its `.tar.gz`/`.tar.bz2` counterpart. Routing by those MIMEs would flood the scan with spurious `ArchiveReadError` warnings for every compressed non-tar file encountered. The only unambiguous archive MIME is `application/x-tar` for plain tars — but its coverage is so narrow (only extensionless or misnamed plain tars, an uncommon scenario) that the complexity cost of a parallel MIME registry, conditional puremagic gating, and the associated tests doesn't pay for itself. Extension aliases handle the real problem cleanly.

### Decision 3 — No native tar encryption support

**Decision: `is_encrypted` is always `False` for tar members; no encryption-skip path is implemented.**

Unlike ZIP (per-member flag bit) or 7z (archive-level header flag), the tar format and its gzip/bzip2/lzma compression wrappers have no native password-protection concept in the stdlib. (Encrypted tarballs in the wild are almost always achieved by piping through a separate tool like GPG — those are out of scope here, same as any other non-archive encrypted blob.) `MemberInfo.is_encrypted` is set to `False` unconditionally by the tar handler. This is a closed decision, not a gap: there is nothing for `tarfile` to detect.

### Decision 4 — Per-member `compressed_size` is always `0`

**Decision: `MemberInfo.compressed_size` is always `0` for tar members; the existing bomb-ratio guard already handles this correctly.**

Tar is a sequential container format — compression (when present) wraps the *entire* stream, not individual members. There is no per-member compressed byte count to report, the same situation `_7z.py` already has for solid 7z archives (`ADR §4.4`: *"`compressed` may be 0 per member in solid archives... the bomb-ratio check already skips the check when `compressed_size == 0`"*). Reusing `compressed_size=0` for every tar member means the existing guard in `_enum_archive.py` (`if compressed_size > 0 and uncompressed_size > compressed_size * 1000`) is a no-op for tar — exactly the precedent already accepted for solid 7z archives. No new code is needed in the enum safety-check loop.

The primary bomb defenses for tar are therefore the same two checks that already cover solid 7z: `max_total_uncompressed_size_mb` (running sum across members) and `max_members`. This is a known, already-accepted limitation of the architecture, not a new one introduced by tar.

### Decision 5 — Symlinks, hardlinks, and device/FIFO members are silently excluded from `list_members()`

**Decision: `TarArchiveHandler.list_members()` omits any `TarInfo` that is not a regular file (`isfile()`) or a directory (`isdir()`).**

Tar, unlike ZIP and 7z, can store symlinks, hardlinks, character/block devices, and FIFOs as members. These have no scannable file content of their own (a symlink's "content" is a path string; a device/FIFO has none at all), and extracting one in isolation (without the rest of the archive present) typically produces either a dangling link or nothing useful for the data-handler chain to read.

Two options were considered:

1. **Extend `MemberInfo` with a new field** (e.g. `is_supported_type: bool`) so the format-neutral model can represent "this entry exists but isn't a regular file or directory," and let the enum safety-check loop in `_enum_archive.py` decide to skip it (consistent with how `is_dir` is currently handled).
2. **Never list them** — the tar handler filters them out inside `list_members()` before returning, so the orchestration layer never sees them.

**Chosen: option 2.** It requires zero changes to `MemberInfo`, `_enum_archive.py`, or any other format module — `ArchiveHandler.list_members()`'s contract ("return all entries (dirs and files)") is satisfied literally, since symlinks/devices/FIFOs are neither. This mirrors how the existing zip/7z handlers never have to represent "this isn't a real file" because the formats themselves don't have non-regular member types. A `logger.debug()` line is emitted per skipped entry (same log level as "no registered handler for this extension" in the ADR's §4 log-level table, since this is a routine "not something we scan" case, not a safety violation).

This is also defense-in-depth alongside extraction-time safety: §4 below covers the `filter="data"` extraction argument, which independently blocks any symlink/hardlink that would resolve outside the destination directory. Filtering at `list_members()` time means a SCAN_ARCHIVE_MEMBER task is never even created for these members — cleaner failure mode than extracting and then erroring out trying to read a dangling symlink.

### Decision 6 — No flatten step in `extract_member()`; recursive cleanup in `_cleanup_temp_workspace()`

**Decision: `extract_member()` returns the actual extracted path (which may be under a subdirectory of `dest_dir`); `_cleanup_temp_workspace()` walks the entire `task_temp` tree recursively rather than one level deep.**

The original ADR §4.9 required a flat layout — each handler renames the extracted file to `dest_dir / Path(member_path).name` and removes empty intermediate directories — because `_cleanup_temp_workspace()` uses `task_temp.iterdir()` (one level only). This constraint drove ~10 lines of rename/rmdir logic in `_7z.py` and would have been repeated in `_tar.py`.

The flatten logic is actually working around a fragility in cleanup, not solving a real requirement. The worker loop doesn't care where under `task_temp` a file lives; it only needs cleanup to cover everything. Fixing cleanup to walk recursively removes the constraint on handlers entirely.

`_cleanup_temp_workspace()` in `_loop.py` changes from:

```python
for path in task_temp.iterdir():   # one level only; subdirectories break this
    secure_delete(path)
try:
    task_temp.rmdir()
except OSError:
    pass
```

to:

```python
for path in task_temp.rglob("*"):
    if path.is_file():             # is_file() guard is required: secure_delete()
        secure_delete(path)        # calls path.unlink() which raises IsADirectoryError
shutil.rmtree(task_temp, ignore_errors=True)
```

`rglob("*")` yields files and directories; `is_file()` ensures `secure_delete()` only sees files (calling `unlink()` on a directory raises `IsADirectoryError` — `missing_ok=True` suppresses only `FileNotFoundError`). `shutil.rmtree()` then removes the now-content-free directory tree. `shutil` is a new import in `_loop.py`.

With this, `extract_member()` in both `_7z.py` and `_tar.py` becomes:

```python
dest_dir.mkdir(parents=True, exist_ok=True)
# ... invoke library extraction call ...
extracted = dest_dir / member_path
if not extracted.exists():
    raise ArchiveReadError(...)
return extracted
```

No rename. No `parent.rmdir()`. No branching on whether the path is already flat. **This change also retroactively simplifies `_7z.py`** (remove the flatten block, lines 47–55 of the current implementation) — making `_7z.py` a small net improvement alongside the new `_tar.py` module.

`_zip.py` is unaffected: it already writes a flat file via `dest.write_bytes(zf.read(member_path))` and recursive cleanup is harmless for flat structures.

### Decision 7 — Extraction uses `filter="data"` (Python 3.14 default, but specified explicitly)

**Decision: `extract_member()` calls `tarfile.TarFile.extract(member, path=dest_dir, filter="data")` explicitly, even though it is the enforced default extraction filter starting in Python 3.14 (PEP 706), which this project already requires (`CLAUDE.md`: Python 3.14+).**

The `"data"` filter is the security-hardened extraction mode: it rejects absolute paths, `..` traversal, and any symlink/hardlink target that would resolve outside the destination directory, and it strips `setuid`/`setgid`/device-file semantics. Since path-traversal members are already rejected earlier in the existing `_enum_archive.py` safety-check loop (check 2, format-agnostic — works unchanged for tar member names), `filter="data"` is a second, independent layer rather than the only line of defense — consistent with the project's existing "defense in depth" pattern of catching unsafe input at enumeration time *and* failing safe at extraction time.

Being explicit (rather than relying on the interpreter default) makes the safety property visible in the code and immune to any future change in Python's default-filter policy.

---

## 3. The Compound-Extension Problem (new constraint, not in the ADR)

### 3a. Why this breaks the existing detection logic

Two call sites currently derive `archive_type` the same naive way:

```python
# _enum_dir.py — both _is_archive_format() and the ENUM_ARCHIVE_MEMBERS payload builder
ext = entry.suffix                          # pathlib: LAST suffix only
ext_bare = ext.lstrip(".").lower()
...
"archive_type": ext.lstrip(".").lower(),     # used as the HANDLER_REGISTRY key
```

`Path("data.tar.gz").suffix` is `".gz"`, not `".tar.gz"`. For single-suffix formats (`archive.zip`, `archive.7z`) this is correct by construction. For tar's compressed variants it is wrong in a way that silently breaks the feature rather than crashing:

| Filename | `Path(...).suffix` | Naive `archive_type` | Correct `archive_type` |
|---|---|---|---|
| `data.tar` | `.tar` | `tar` | `tar` ✅ (already correct) |
| `data.tgz` | `.tgz` | `tgz` | `tar` ❌ |
| `data.tbz2` | `.tbz2` | `tbz2` | `tar` ❌ |
| `data.txz` | `.txz` | `txz` | `tar` ❌ |
| `data.tar.gz` | `.gz` | `gz` | `tar` ❌ |
| `data.tar.bz2` | `.bz2` | `bz2` | `tar` ❌ |
| `data.tar.xz` | `.xz` | `xz` | `tar` ❌ |

With no fix, `data.tar.gz` would never be routed to `ENUM_ARCHIVE_MEMBERS` at all (`_is_archive_format()` checks `ext_bare in HANDLER_REGISTRY`, and `"gz"` is not a registered key) — it would simply fall through to the regular file-handler lookup, find no handler for `.gz`, and be silently skipped. This is a correctness gap, not a crash, which makes it easy to miss in testing if fixtures only cover bare `.tar`.

### 3b. Design: `HANDLES` per-module self-declaration, longest-first `endswith` registry lookup

Each archive handler module declares a `HANDLES` dict listing its recognized file extensions, mirroring the pattern used by every file handler in `filehandlers/`:

```python
# archivehandlers/_tar.py
HANDLES = {
    "ext": [".tar", ".tgz", ".tbz2", ".tbz", ".txz", ".tar.gz", ".tar.bz2", ".tar.xz"],
}

# archivehandlers/_zip.py  (add to existing module)
HANDLES = {
    "ext": [".zip"],
}

# archivehandlers/_7z.py  (add to existing module)
HANDLES = {
    "ext": [".7z"],
}
```

`archivehandlers/__init__.py` builds a detection registry from those declarations:

```python
# Collect (bare_ext_without_dot, archive_type) pairs from every handler module,
# sorted longest-first so "tar.gz" matches before the hypothetical shorter "gz"
# would if it were ever registered. Longest-first is the only correct order for
# compound-suffix matching via endswith().
_EXT_REGISTRY: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            (ext.lstrip(".").lower(), mod.ARCHIVE_TYPE)
            for mod in (_zip, _7z, _tar)
            for ext in mod.HANDLES["ext"]
        ),
        key=lambda pair: -len(pair[0]),
    )
)


def detect_archive_type(filename: str) -> str | None:
    """Map a filename to a registered archive_type, or None if unrecognized.

    Handles single-suffix formats (data.zip -> "zip") and tar's compound
    and aliased suffixes (data.tar.gz -> "tar", data.tgz -> "tar").
    Only returns a type whose handler is present in HANDLER_REGISTRY.
    """
    name = filename.lower()
    for bare_ext, archive_type in _EXT_REGISTRY:
        if name.endswith(f".{bare_ext}") and archive_type in HANDLER_REGISTRY:
            return archive_type
    return None
```

**Why `endswith` and not a dict lookup?** File handler extensions are all single-suffix (`.xlsx`, `.pdf`), so `filehandlers/__init__.py` can use an exact-match dict keyed on `Path(name).suffix`. Archive handlers include compound extensions (`.tar.gz`) that `Path.suffix` can't capture. A dict keyed on `".tar.gz"` with exact-match lookup would never fire because `entry.suffix` gives `".gz"`. The `endswith(f".{bare_ext}")` approach with longest-first ordering handles both single-suffix and compound cases correctly — `notes.gz` correctly misses `"tar.gz"` even though it ends with `".gz"`, and `report.2024.tar.gz` correctly hits `"tar.gz"` despite the extra dot-segment before `.tar`. A `.suffixes`-list based approach (checking that the last two suffixes are `[".tar", ".gz"]`) would also work but is more fragile for names with extra dots.

`_enum_dir.py` replaces the suffix-based archive check with a filename-based one:

```python
# Before (zip/7z only, ADR §4.7):
def _is_archive_format(ext: str, config: Config) -> bool:
    if not config.archives.enabled:
        return False
    ext_bare = ext.lstrip(".").lower()
    if "all" in config.archives.formats:
        from piidigger.archivehandlers import HANDLER_REGISTRY
        return ext_bare in HANDLER_REGISTRY
    return ext_bare in {fmt.lower().lstrip(".") for fmt in config.archives.formats}

# After:
def _detect_archive_type(filename: str, config: Config) -> str | None:
    if not config.archives.enabled:
        return None
    from piidigger.archivehandlers import detect_archive_type
    archive_type = detect_archive_type(filename)
    if archive_type is None:
        return None
    if "all" in config.archives.formats:
        return archive_type
    return archive_type if archive_type in {f.lower() for f in config.archives.formats} else None
```

The call site in `handle_enum_dir` switches from `_is_archive_format(ext, config)` + `ext.lstrip(".").lower()` to a single `_detect_archive_type(entry.name, config)` call that serves both the routing decision and the `archive_type` payload value — removing the duplicate derivation that exists today.

### 3c. `formats` config semantics unaffected

`config.archives.formats = ["tar"]` (or `"all"`) continues to mean "the tar registry entry is enabled" — exactly like today's `["zip"]`/`["7z"]` entries — regardless of which compression flavor a given file uses. No new TOML keys are needed; `generate_toml_template()`'s `[archives]` block is unchanged (still `formats = ["all"]` by default).

---

## 4. Detailed Design — `archivehandlers/_tar.py`

`extract_member()` lets `tarfile` extract to whatever path structure it creates under `dest_dir` and returns that path directly. There is no flatten/rename step — recursive cleanup in `_cleanup_temp_workspace()` (Decision 6) handles any subdirectory structure regardless of how it got there.

```python
from __future__ import annotations

import tarfile
from pathlib import Path

from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "tar"
HANDLES = {
    "ext": [".tar", ".tgz", ".tbz2", ".tbz", ".txz", ".tar.gz", ".tar.bz2", ".tar.xz"],
}


class TarArchiveHandler:
    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        try:
            with tarfile.open(archive_path, mode="r:*") as tf:
                members = []
                for info in tf.getmembers():
                    if not (info.isfile() or info.isdir()):
                        # symlinks, hardlinks, device/FIFO nodes: no scannable
                        # content; excluded at list time so no task is ever
                        # created for them (Decision 5)
                        continue
                    members.append(
                        MemberInfo(
                            name=info.name,
                            uncompressed_size=info.size,
                            compressed_size=0,  # tar has no per-member compressed size
                            is_dir=info.isdir(),
                            is_encrypted=False,  # tar has no native encryption
                        )
                    )
                return members
        except (tarfile.TarError, OSError) as exc:
            raise ArchiveReadError(str(exc)) from exc

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path, mode="r:*") as tf:
                member = tf.getmember(member_path)
                tf.extract(member, path=dest_dir, filter="data")
            extracted = dest_dir / member_path
            if not extracted.exists():
                raise ArchiveReadError(
                    f"member {member_path!r} not found after extraction from {archive_path}"
                )
            return extracted
        except ArchiveReadError:
            raise
        except (tarfile.TarError, OSError, KeyError) as exc:
            raise ArchiveReadError(str(exc)) from exc


handler = TarArchiveHandler()
```

Notes:

- `mode="r:*"` is used for both `list_members()` and `extract_member()` — transparent compression detection, no branching on file extension needed inside the handler at all. The handler is fully self-contained; it never looks at `archive_path.suffix`.
- No flatten/rename step. `dest_dir / member_path` may be under a subdirectory (e.g. `task_temp/reports/file.txt` for a member named `reports/file.txt`). That is fine — `_cleanup_temp_workspace()` walks the entire tree with `rglob("*")` and `shutil.rmtree()`.
- `tf.getmember(member_path)` raises `KeyError` on a missing name, already caught alongside `tarfile.TarError`/`OSError`.
- No `import py7zr`-style lazy-import-with-`ImportError`-handling needed — `tarfile` is always present (stdlib), unlike `_7z.py`.
- `tarfile.extractfile()` supports in-memory reads, but per ADR revision 2, all formats use the unified temp-dir extraction path for consistency — `_tar.py` does not special-case an in-memory shortcut.

---

## 5. Files Changed

| File | Nature of change |
|---|---|
| `src/piidigger/archivehandlers/_tar.py` | **New** — `TarArchiveHandler` with `list_members()` + `extract_member()`, plus `HANDLES = {"ext": [...]}` declaration |
| `src/piidigger/archivehandlers/_zip.py` | Add `HANDLES = {"ext": [".zip"]}` (existing handler, no logic change) |
| `src/piidigger/archivehandlers/_7z.py` | Add `HANDLES = {"ext": [".7z"]}` (existing handler, no logic change) |
| `src/piidigger/archivehandlers/__init__.py` | Add `_tar` to the registration loop; build `_EXT_REGISTRY` from each module's `HANDLES["ext"]`; add `detect_archive_type()` (§3b) |
| `src/piidigger/orchestration/worker/_enum_dir.py` | Replace `_is_archive_format(ext, config)` with `_detect_archive_type(filename, config)`; payload's `archive_type` comes directly from the detector instead of `ext.lstrip(".").lower()` |
| `src/piidigger/orchestration/worker/_enum_archive.py` | **No change** — already format-agnostic via `get_handler(payload.archive_type)`; `_NESTED_ARCHIVE_EXTS` derivation from `HANDLER_REGISTRY` keys continues to work (gains `".tar"` automatically) |
| `src/piidigger/orchestration/worker/_scan_archive_member.py` | **No change** — already format-agnostic |
| `src/piidigger/models/payloads.py` | **No change** — `archive_type: str = "zip"` field already accepts arbitrary strings; default stays `"zip"` for backward compatibility, same reasoning as the 7z addition |
| `src/piidigger/models/config.py` | **No change** — `ArchiveConfig.formats` default is already `["all"]`; no new fields needed |
| `pyproject.toml` | **No change** — `tarfile` is stdlib; `archivehandlers.*` is already in the mypy strict overrides block (added for 7z) |
| `testdata/tar/create_fixtures.py` | **New** — fixture creation script |
| `testdata/tar/*.tar`, `*.tar.gz`, `*.tar.bz2`, `*.tar.xz` | **New** — test fixture files |
| `tests/test_archives.py` | Add tar handler unit tests, `detect_archive_type()` tests, enum/scan integration tests, and the symlink-exclusion test |
| User guide | Extend the existing Security Considerations archive-extraction note (`ADR §4.12`) to mention tar — no new disclosure needed since the temp-dir + secure-delete model is unchanged |

| `src/piidigger/orchestration/worker/_loop.py` | `_cleanup_temp_workspace()`: `iterdir()` → `rglob("*")` with `is_file()` guard + `secure_delete()`; `rmdir()` → `shutil.rmtree()`; add `import shutil` |
| `src/piidigger/archivehandlers/_7z.py` | Remove flatten/rename/`parent.rmdir()` block from `extract_member()` (lines 47–55); return `dest_dir / member_path` directly |

**No changes to**: `coordinator.py`, `worker/_loop.py` dispatch table (already wired generically for `ENUM_ARCHIVE_MEMBERS`/`SCAN_ARCHIVE_MEMBER`), `progress.py`, output handlers, data handlers, file handlers, CLI, `protocols.py` (`ArchiveHandler` protocol is already format-neutral), `models/archive.py` (`MemberInfo` needs no new fields — see Decision 5), `models/results.py`.

This file list is short specifically because the ADR's registry pattern is doing its job: the only non-trivial change outside the new handler module itself is the extension-detection fix in §3, which is a tar-specific gap in an assumption the ADR didn't need to make for single-suffix formats.

---

## 6. Test Plan

### 6.1 New tests in `tests/test_archives.py`

Following the existing naming convention (`test_enum_archive_7z_*`, `test_scan_archive_member_7z_*`, `test_7z_handler_*`):

**`_cleanup_temp_workspace()` update (new test):**

| Test | What it verifies |
|---|---|
| `test_cleanup_temp_workspace_recursive` | Files in subdirectories of `task_temp` are secure-deleted and the full directory tree is removed — proves the `rglob` + `shutil.rmtree` path (this was untestable with the old `iterdir()` approach since handlers always flattened) |

**Handler unit tests:**

| Test | What it verifies |
|---|---|
| `test_tar_handler_list_members` | Plain `.tar`, attribute access matches `MemberInfo` fields |
| `test_tar_handler_extract_member` | Extracted bytes match source content; flat path returned |
| `test_tar_handler_corrupt_raises_archive_read_error` | Non-tar bytes → `ArchiveReadError` |
| `test_tar_handler_list_members_excludes_symlinks` | A symlink member in the archive does not appear in `list_members()` output (Decision 5) |
| `test_tar_handler_transparent_gzip` | `.tar.gz` opens and lists correctly via `mode="r:*"` |
| `test_tar_handler_transparent_bzip2` | `.tar.bz2` opens and lists correctly |
| `test_tar_handler_transparent_xz` | `.tar.xz` opens and lists correctly |

**Extension-detection tests (new — not in the ADR's existing zip/7z suite, since this is the new layer from §3):**

| Test | What it verifies |
|---|---|
| `test_detect_archive_type_tar` | `data.tar` → `"tar"` |
| `test_detect_archive_type_tar_gz_compound_suffix` | `data.tar.gz` → `"tar"` |
| `test_detect_archive_type_tgz_alias` | `data.tgz` → `"tar"` |
| `test_detect_archive_type_tbz2_alias` | `data.tbz2` → `"tar"` |
| `test_detect_archive_type_txz_alias` | `data.txz` → `"tar"` |
| `test_detect_archive_type_plain_gz_not_tar` | `notes.gz` (no `.tar` segment) → `None`, not misdetected as tar |
| `test_detect_archive_type_zip_unaffected` | `archive.zip` → `"zip"` (regression check: existing single-suffix detection still works) |
| `test_detect_archive_type_unknown_returns_none` | `data.rar` → `None` |

**Enum/scan integration tests (mirrors the existing 7z suite):**

| Test | What it verifies |
|---|---|
| `test_enum_archive_tar_simple_pii_emits_scan_task` | `list_members()` path; correct task emitted |
| `test_enum_archive_tar_gz_simple_pii_emits_scan_task` | Same, via compressed variant — proves end-to-end routing, not just the handler unit |
| `test_enum_archive_tar_corrupt_returns_error` | `ArchiveReadError` on list → `status="error"` result |
| `test_enum_archive_tar_oversize_member_skipped` | Size limit enforced same as zip/7z |
| `test_enum_archive_tar_member_count_limit` | Same pattern as 7z's `max_members=3` test |
| `test_enum_archive_tar_traversal_member_rejected` | `../` member name rejected by the existing format-agnostic check 2 |
| `test_enum_archive_tar_symlink_member_not_scanned` | A tar containing a symlink produces zero `SCAN_ARCHIVE_MEMBER` tasks for it (end-to-end proof of Decision 5, distinct from the handler-unit-level test above) |
| `test_scan_archive_member_tar_finds_pii` | End-to-end: extract → scan → finding with correct PAN |
| `test_scan_archive_member_tar_lineage` | `source_container_type == "tar"` in finding (note: `"tar"` regardless of whether the source file was `.tar.gz` or `.tbz2` — Decision 1) |
| `test_enum_dir_tar_gz_file_emits_enum_archive_task` | `.tar.gz` file routed to `ENUM_ARCHIVE_MEMBERS` with `archive_type="tar"` — proves the §3 fix end-to-end |
| `test_enum_dir_tgz_file_emits_enum_archive_task` | `.tgz` alias routed correctly |
| `test_enum_dir_zip_and_tar_gz_in_same_dir` | Mixed directory → correct `archive_type` per file (regression: zip detection still works alongside the new tar detector) |

**One existing assertion unaffected:** `test_archive_config_defaults` (`assert cfg.formats == ["all"]`) needs no change — tar joins the existing `"all"` expansion automatically once registered.

### 6.2 Test fixtures in `testdata/tar/`

Script: `testdata/tar/create_fixtures.py`, following the exact pattern of `testdata/7z/create_fixtures.py` (`_write()` helper, `_verify()` step, deterministic output committed to the repo).

| Fixture | Purpose |
|---|---|
| `simple-pii.tar` | Uncompressed; text member with known PAN — happy path, proves baseline `tarfile` handling |
| `simple-pii.tar.gz` | Same content, gzip-compressed — proves `mode="r:*"` transparent gzip detection |
| `simple-pii.tar.bz2` | Same content, bzip2-compressed — proves transparent bzip2 detection |
| `simple-pii.tar.xz` | Same content, xz-compressed — proves transparent lzma detection |
| `corrupt.tar` | Non-tar bytes — error path |
| `many-members.tar` | 5 members — member count limit test (gzip variant not duplicated; one compression flavor is enough to prove the limit logic, which is format-agnostic) |
| `oversize-member.tar.gz` | 100 MB of zeros, gzip-compressed (compresses to a few KB on disk) — size limit test, mirrors the 7z fixture's rationale |
| `traversal-member.tar` | A member named `../traversal.txt`. **Simpler to construct than the ZIP equivalent**: `tarfile.TarInfo` does not sanitize member names the way `zipfile.ZipFile.writestr()` does, so this fixture can be built directly with `tarfile.TarFile.addfile(TarInfo(name="../traversal.txt"), fileobj=...)` — no raw byte-patching required (contrast with `ZIP_HANDLING_PLAN.md §9`'s note that the ZIP traversal fixture needs manual central-directory construction) |
| `symlink-member.tar` | One regular file member plus one symlink member (`TarInfo(type=tarfile.SYMTYPE)`) pointing at the regular file — proves Decision 5's exclusion without needing the regular member to also fail |

`create_fixtures.py` writes each compressed variant with the matching stdlib module (`tarfile.open(path, "w:gz")`, `"w:bz2"`, `"w:xz")`) and verifies each is re-openable with `mode="r:*"` as the final step, same as the 7z script's `_verify()`.

---

## 7. Implementation Sequence

| # | Step |
|---|---|
| 1 | `orchestration/worker/_loop.py` — update `_cleanup_temp_workspace()` to recursive walk; add `import shutil` |
| 2 | `archivehandlers/_7z.py` — remove flatten block from `extract_member()`; return `dest_dir / member_path` directly; add `HANDLES = {"ext": [".7z"]}` |
| 3 | `archivehandlers/_zip.py` — add `HANDLES = {"ext": [".zip"]}` (no logic change) |
| 4 | `archivehandlers/_tar.py` — `TarArchiveHandler`: `list_members()`, `extract_member()` (no flatten), `HANDLES = {"ext": [...]}` |
| 5 | `archivehandlers/__init__.py` — register `_tar`; build `_EXT_REGISTRY` from modules' HANDLES; add `detect_archive_type()` |
| 6 | `orchestration/worker/_enum_dir.py` — replace `_is_archive_format()` with `_detect_archive_type()`; update payload construction |
| 4 | `testdata/tar/create_fixtures.py` — write and run; commit fixture binaries |
| 5 | `tests/test_archives.py` — handler unit tests, `detect_archive_type()` tests, enum/scan integration tests, symlink-exclusion tests |
| 6 | `uv run ruff check src/ tests/ && uv run mypy src/ && uv run pytest tests/ -v` |
| 7 | User documentation — confirm the existing Security Considerations entry (`ADR §4.12`) reads correctly with tar included ("ZIP, 7z, and others" already covers this; verify no format-specific wording needs updating) |

---

## 8. Exit Criteria

- `.tar`, `.tar.gz`/`.tgz`, `.tar.bz2`/`.tbz2`, and `.tar.xz`/`.txz` all route to `ENUM_ARCHIVE_MEMBERS` with `archive_type="tar"`
- `archivehandlers.HANDLER_REGISTRY["tar"]` round-trips list/extract correctly for all four compression flavors
- Symlink, hardlink, and device/FIFO members are excluded from enumeration — never produce a `SCAN_ARCHIVE_MEMBER` task
- Path traversal, oversize, and member-count safety checks apply to tar with zero new code in `_enum_archive.py` (proving the format-agnostic safety loop genuinely is format-agnostic)
- Findings from tar members carry `source_container_type="tar"` lineage regardless of compression flavor
- No new third-party dependency added to `pyproject.toml`
- Zero changes to `coordinator.py`, `worker/_loop.py` dispatch, `protocols.py`, or `models/archive.py`
- `ruff` + `mypy --strict` (for `archivehandlers.*`) clean; full test suite passes
