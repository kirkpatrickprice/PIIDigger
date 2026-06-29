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
    depth: int = Field(default=0, ge=0, le=3)


class ScanArchiveMemberPayload(PiiDiggerModel):
    model_config = ConfigDict(frozen=True)

    archive_path: Path
    member_path: str
    ext: str
    mime: str | None
    uncompressed_size: int = Field(ge=0)
    depth: int = Field(default=1, ge=1, le=4)
