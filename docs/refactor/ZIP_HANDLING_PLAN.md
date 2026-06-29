# ZIP File Handling Plan for Refactor

**Branch**: `refactor`  
**Status**: Design Ready for Future Implementation  
**Last Updated**: 2026-06-14

## 1. Purpose

Capture implementation details for ZIP archive support in the Task Queue refactor.
This document targets future execution work, not current pre-refactor code.

## 2. Why ZIP Support Waits for Refactor

Current architecture has hard limits for archive support.

1. `classes.File` expects a real `pathlib.Path` and filesystem metadata (`stat`, `parent`, `suffix`).
2. `filescan.findFilesWorker` and `fileHandlerDispatcher` pass only host filesystem paths.
3. Handler selection is extension/MIME based for top-level files, not archive members.
4. SENTINEL-based worker shutdown makes recursive task fan-out brittle.

Result: ZIP can be added safely after Task Queue + Worker Pool is in place.

## 3. Scope

### In Scope

- `.zip` container traversal
- Nested member scanning for existing supported file handlers
- Configurable recursion depth for nested archives
- Resource limits for archive safety
- Progress and logging visibility for archive processing
- Reporting both ZIP archive file and member file (e.g. `archive.zip::path/to/credit-cards.txt`)

### Out of Scope (Initial ZIP milestone)

- Writing back modified archive content
- Password-protected ZIP decryption
- Non-ZIP archive formats (`.7z`, `.rar`, `.tar.*`) - although these should follow the same pattern once appropriate libraries are added to PIIDigger.  
- Cross-archive deduplication by content hash

## 4. Design Principles

1. Design and implementation decisions made to add ZIP support must facilitate implementation of other archive formats (7z, RAR, tar*, etc).
2. **Task-first expansion**: Archive scanning creates new tasks, not recursive local loops.
3. **Boundary isolation**: Extraction logic stays in an archive service/adapter layer.
4. **Deterministic limits**: Depth, size, and member count are hard-bounded by config.
5. **Fail-open for scan continuity**: A bad archive member logs and skips without stopping run.
6. **In-memory archive extraction**: Archive extraction should not consume disk resources unless unavoidable.  A memory-based (maybe using streaming) implementation is preferred.
7. **No shared temp state**: If disk resources are required, archive members are extracted only when needed, and workers use isolated temp paths and explicit cleanup.
8. **Secure deletion**: If disk resources are required, temp files must be securely deleted (file overwrite before delete) immediately.  Under no circumstances will extracted archive members persist on disk as this could potentially create additional, permanant copies of unmanaged PII.

## 5. Proposed Task Model Additions

Add task types to the refactor queue model:

- `ENUM_ARCHIVE_MEMBERS`
- `SCAN_ARCHIVE_MEMBER`
- `SCAN_ARCHIVE_TEXT_CHUNK` (optional, if member stream conversion is split)

### Proposed Task Payloads

```python
from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field


class ArchiveOrigin(BaseModel):
    """Top-level archive context for logs and result lineage."""

    archive_path: Path
    archive_sha256: str | None = None


class ArchiveMemberRef(BaseModel):
    """Reference to one logical member path inside an archive."""

    origin: ArchiveOrigin
    member_path: str
    compressed_size: int = Field(ge=0)
    uncompressed_size: int = Field(ge=0)
    depth: int = Field(ge=0, le=3)


class EnumArchiveMembersPayload(BaseModel):
    """Input to member enumeration task."""

    origin: ArchiveOrigin
    archive_path: Path
    depth: int = Field(ge=0, le=3)


class ScanArchiveMemberPayload(BaseModel):
    """Input to archive member scanning task."""

    member_ref: ArchiveMemberRef
    max_chunk_count: int = Field(ge=1, le=64)
```

## 6. Handler Routing Strategy

### Current Limitation

`globalfuncs.getFileHandlerName(ext, mime)` assumes host file path metadata.
Archive members need extension/MIME checks from member names and stream headers.

### Planned Strategy

1. Build a lightweight `VirtualFileDescriptor` for archive members:
   - `display_path`: `archive.zip::path/in/archive.txt`
   - `ext`: member suffix
   - `mime`: optional sniffed MIME
   - `size`: uncompressed size

2. Reuse existing file handler routing by adapting descriptor fields.

3. Use stream-capable readers in handlers where possible.

4. For handlers requiring a real path, extract to a per-task temp file and clean up.

## 7. Extraction and Streaming Approach

Use Python `zipfile` for initial support.

### Preferred Flow

1. Open archive with `zipfile.ZipFile`.
2. Enumerate members with guard checks.
3. For text-like files, stream bytes and decode using current encoding strategy.
4. For binary formats (pdf/docx/xlsx/xls), extract member to temp file and call existing handler.
5. Emit standard scan results with lineage metadata.

### Lineage Fields for Results

Add metadata fields so outputs stay traceable:

- `source_path`: host archive path
- `source_member_path`: member path in archive
- `source_depth`: nesting level
- `source_container_type`: `zip`

## 8. Safety Controls

ZIP support must ship with explicit safeguards.

### Required Limits

- `max_archive_depth` (default: `1`, max: `3`)
- `max_archive_members` (default: `10000`)
- `max_member_uncompressed_size_mb` (default: `50`)
- `max_archive_total_uncompressed_size_mb` (default: `1024`)
- `archive_task_timeout_seconds` (default: `30`)

### Required Rejection Rules

- Reject path traversal members (`../`, absolute paths)
- Reject encrypted members for first milestone
- Reject members exceeding configured limits
- Reject archives with invalid central directory data

### Bomb Resistance Heuristics

- Compression ratio cap per member (example: `>1000x` reject)
- Running sum cap of projected uncompressed bytes
- Hard stop when member count exceeds threshold

## 9. Progress and Logging Requirements

Archive work must surface in progress channels and logs.

### New Counters

- `archives_found`
- `archives_scanned`
- `archive_members_found`
- `archive_members_scanned`
- `archive_members_skipped`
- `archive_errors`

### Log Event Requirements

- Archive opened/closed with duration
- Member skipped with reason and limit key
- Task timeout for archive member scan
- Temp extraction path creation and cleanup failures

## 10. Configuration Additions

Add section to runtime config model and config file:

```toml
[archives]
enabled = true
formats = ["zip"]
max_depth = 1
max_members = 10000
max_member_uncompressed_size_mb = 50
max_total_uncompressed_size_mb = 1024
task_timeout_seconds = 30
extract_binary_members_to_temp = true
```

### CLI Considerations

Potential flags for parity and tuning:

- `--archives-enabled/--no-archives`
- `--archive-max-depth`
- `--archive-max-members`
- `--archive-max-member-size-mb`

## 11. Proposed Implementation Sequence

### Phase A: Foundations

- Add archive config model and defaults
- Add new task types and payload models
- Add archive counters and logging schema

### Phase B: ZIP Enumeration

- Implement `ENUM_ARCHIVE_MEMBERS` handler
- Enforce traversal and size/member limits
- Emit `SCAN_ARCHIVE_MEMBER` tasks

### Phase C: Member Scan Integration

- Implement member scan adapter and descriptor mapping
- Route member content through existing handler system
- Attach lineage metadata to findings

### Phase D: Hardening

- Add timeout and retry policy for archive tasks
- Add metrics for skipped members and rejection causes
- Validate graceful shutdown during large archive scans

## 12. Testing Requirements for ZIP Feature

### Unit Tests

- Parse ZIP member list from valid archive
- Reject traversal member names
- Reject encrypted member entries
- Enforce each configured limit independently
- Validate compression ratio rejection logic

### Integration Tests

- Scan ZIP with plaintext, JSON, XML members containing known PII
- Scan ZIP with unsupported member types and verify skip behavior
- Scan ZIP containing supported binary documents via temp extraction path
- Verify output lineage fields for all findings from members

### Resilience Tests

- Corrupt ZIP central directory handling
- Timeout during member scan
- Worker crash during archive scan task
- Interrupt (Ctrl+C) during large archive enumeration

### Fixture Set to Add in `testdata/zip/`

- `simple-pii.zip` (known findings)
- `nested-depth-2.zip`
- `oversize-member.zip`
- `many-members.zip`
- `traversal-member.zip`
- `encrypted-member.zip`
- `corrupt.zip`
- `zip-bomb-simulated.zip` (safe synthetic for limit tests)

## 13. Open Decisions

1. Should nested ZIP scanning be enabled in milestone one or deferred?
2. Should binary member extraction always use temp files, or add stream adapters first?
3. Should archive lineage fields be optional or mandatory in all output handlers?
4. Should unsupported archive members emit debug logs only or warning logs?

## 14. Code Touchpoints (Planned)

- `src/piidigger/orchestration/tasks.py` (new task models)
- `src/piidigger/orchestration/handlers.py` (archive handlers)
- `src/piidigger/orchestration/coordinator.py` (task fan-out)
- `src/piidigger/classes.py` (config model updates)
- `src/piidigger/globalfuncs.py` (virtual descriptor routing support)
- `src/piidigger/piidigger.py` (counter wiring/progress integration)
- `tests/` (unit + integration + resilience coverage)

## 15. Exit Criteria for ZIP Milestone

- ZIP enumeration and member scanning run under task queue architecture.
- Archive safety limits are active and verified by tests.
- Findings from archive members include lineage fields in outputs.
- Timeout and graceful shutdown behavior pass resilience tests.
- No regression in existing non-archive scan paths.
