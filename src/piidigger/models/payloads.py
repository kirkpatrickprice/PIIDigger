from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict

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
