from __future__ import annotations

from piidigger.models.base import PiiDiggerModel


class ResultRecord(PiiDiggerModel):
    source_path: str
    source_member_path: str | None = None  # None for on-disk files
    source_depth: int = 0  # 0 for on-disk; >=1 for archive members
    source_container_type: str | None = None  # "zip" for archive members; None for on-disk
    handler: str  # data handler name, e.g. "pan", "email"
    matches: dict[str, list[str]]  # match type → list of matched values
