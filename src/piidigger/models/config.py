from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Config(BaseModel):
    """Scan configuration.

    Phase 2 stub — holds only the fields the coordinator needs now.
    Phase 3 will replace this with the full validated model (exclude_dirs,
    data_handlers, max_workers, log_file, etc.) and proper TOML loading.

    Note: Phase 3 must normalize start_dirs consistently as list[str] where
    ["all"] means "scan all drives/roots".  The 1.x config allowed the bare
    string "all" which is ambiguous; that form must be rejected or converted.
    """

    start_dirs: list[Path] = []
