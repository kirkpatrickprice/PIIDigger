from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field

from piidigger.models.base import PiiDiggerModel


class EnumDirPayload(PiiDiggerModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    depth: int = 0


class ScanFilePayload(PiiDiggerModel):
    model_config = ConfigDict(frozen=True)

    display_path: str
    file_path: Path
    ext: str
    mime: str | None
    size: int
    depth: int = 0


class EnumArchiveMembersPayload(PiiDiggerModel):
    model_config = ConfigDict(frozen=True)

    archive_path: Path
    archive_type: str = "zip"
    depth: int = Field(default=0, ge=0, le=3)


class ScanArchiveMembersPayload(PiiDiggerModel):
    """One batch: a contiguous run of members from one archive.

    The member paths themselves travel in Task.items, not here, because the
    coordinator shrinks that list as members finish and the payload is never
    changed.  depth is the depth of the members, one below the archive.
    """

    model_config = ConfigDict(frozen=True)

    archive_path: Path
    archive_type: str = "zip"
    depth: int = Field(default=1, ge=1, le=4)
