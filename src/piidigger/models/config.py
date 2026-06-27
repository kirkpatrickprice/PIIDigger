from __future__ import annotations

import os
import platform
import string
import tomllib
from pathlib import Path

from pydantic import Field, ValidationError

from piidigger.models.base import PiiDiggerModel


class ResultsConfig(PiiDiggerModel):
    """Output destination and format selection.

    path is the folder where output files are written.
    formats selects which output formats to produce; ["all"] enables every format.
    Valid format names: "csv", "json", "text".
    Filenames are generated automatically: piidigger-<timestamp>.<ext>
    """

    path: Path = Path("piidigger-results")
    formats: list[str] = Field(default_factory=lambda: ["all"])


def _default_start_dirs() -> list[Path]:
    """Return OS-appropriate default start directories."""
    system = platform.system().lower()
    if system == "windows":
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return [Path("/")]


_WINDOWS_EXCLUDE_DIRS: list[str] = [
    "C:\\Windows",
    "C:\\Program Files (x86)",
    "C:\\Program Files",
]

_DARWIN_EXCLUDE_DIRS: list[str] = [
    "/dev",
    "/etc",
    "/usr/bin",
    "/usr/local/Homebrew",
    "/usr/lib",
    "/usr/sbin",
    "/Applications",
    "/Library/Developer",
    "/Library/Documentation",
    "/System",
]

_LINUX_EXCLUDE_DIRS: list[str] = [
    "/boot",
    "/dev",
    "/etc",
    "/proc",
    "/run",
    "/snap",
    "/sys",
    "/usr/bin",
    "/usr/lib",
    "/usr/lib32",
    "/usr/lib64",
    "/usr/libx32",
    "/usr/local",
    "/usr/sbin",
    "/usr/share",
    "/usr/src",
    "*/.vscode-server",
    "/mnt/c",
    "/mnt/d",
    "/mnt/wslg",
    "/wsl",
]


def _default_exclude_dirs() -> list[str]:
    """Return OS-appropriate default exclude directories."""
    system = platform.system().lower()
    if system == "windows":
        return _WINDOWS_EXCLUDE_DIRS
    if system == "darwin":
        return _DARWIN_EXCLUDE_DIRS
    return _LINUX_EXCLUDE_DIRS



class Config(PiiDiggerModel):
    """2.0 scan configuration.

    Replaces the 1.x classes.Config getter-soup with a validated Pydantic model.
    Use Config.from_toml() to load from a TOML file, or Config.default() for
    built-in defaults.  Construct directly in tests with explicit field values.
    """

    start_dirs: list[Path] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(default_factory=list)
    # ["all"] means all extensions/MIME types registered in filehandlers
    include_exts: list[str] = Field(default_factory=lambda: ["all"])
    include_mime: list[str] = Field(default_factory=lambda: ["all"])
    # ["all"] means all data handlers in datahandlers.HANDLER_REGISTRY
    data_handlers: list[str] = Field(default_factory=lambda: ["all"])
    max_workers: int = Field(default_factory=lambda: os.cpu_count() or 1)
    default_timeout_seconds: int = Field(default=30, ge=1, le=600)
    local_files_only: bool = True
    log_file: Path = Path("logs/piidigger.log")
    log_level: str = "INFO"
    results: ResultsConfig = Field(default_factory=ResultsConfig)

    @classmethod
    def from_toml(cls, path: Path) -> Config:
        """Load and validate a Config from a TOML file.

        Raises ValueError with a clear message on:
        - file not found
        - TOML parse error
        - validation error (wrong types, unknown fields)
        - start directory that does not exist
        """
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except FileNotFoundError as e:
            raise ValueError(f"config file not found: {path}") from e
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"invalid TOML in {path}: {e}") from e

        try:
            config = cls.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"invalid configuration in {path}:\n{e}") from e

        for d in config.start_dirs:
            if not d.exists():
                raise ValueError(f"start directory does not exist: {d}")

        return config

    @classmethod
    def default(cls) -> Config:
        """Return a Config populated with built-in defaults.

        start_dirs and exclude_dirs are OS-appropriate.  Output paths point to
        a piidigger-results/ subdirectory of the current working directory.
        """
        return cls(
            start_dirs=_default_start_dirs(),
            exclude_dirs=_default_exclude_dirs(),
            include_exts=["all"],
            include_mime=["all"],
            data_handlers=["all"],
            results=ResultsConfig(),
        )

    def to_toml_str(self) -> str:
        """Serialize this Config to a TOML-formatted string.

        Suitable for writing to a file with config generate.
        Note: the json_file field is written as ``json = ...`` for TOML
        compatibility (matching what from_toml() expects).
        """
        lines: list[str] = []

        def _fmt(val: object) -> str:
            if isinstance(val, bool):
                return str(val).lower()
            if isinstance(val, str):
                return f'"{val}"'
            if isinstance(val, Path):
                return f'"{val.as_posix()}"'
            if isinstance(val, int):
                return str(val)
            if isinstance(val, list):
                items = ", ".join(_fmt(v) for v in val)
                return f"[{items}]"
            return f'"{val}"'

        lines.append(f"start_dirs = {_fmt([d.as_posix() for d in self.start_dirs])}")
        lines.append(f"exclude_dirs = {_fmt(self.exclude_dirs)}")
        lines.append(f"include_exts = {_fmt(self.include_exts)}")
        lines.append(f"include_mime = {_fmt(self.include_mime)}")
        lines.append(f"data_handlers = {_fmt(self.data_handlers)}")
        lines.append(f"max_workers = {self.max_workers}")
        lines.append(f"default_timeout_seconds = {self.default_timeout_seconds}")
        lines.append(f"local_files_only = {str(self.local_files_only).lower()}")
        lines.append(f'log_file = "{self.log_file.as_posix()}"')
        lines.append(f'log_level = "{self.log_level}"')
        lines.append("")
        lines.append("[results]")
        lines.append(f"path = {_fmt(self.results.path)}")
        lines.append(f"formats = {_fmt(self.results.formats)}")

        return "\n".join(lines) + "\n"
