from __future__ import annotations

import difflib
import platform
import string
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from piidigger.models.base import PiiDiggerModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WINDOWS_START_DIRS: list[str] = ["all"]
_DARWIN_START_DIRS: list[str] = ["/"]
_LINUX_START_DIRS: list[str] = ["/"]

_WINDOWS_EXCLUDE_DIRS: list[str] = [
    "C:/Windows",
    "C:/Program Files (x86)",
    "C:/Program Files",
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

# Defaults — single source of truth shared by model field definitions and
# generate_toml_template().  Changing a value here updates both automatically.
_DEFAULT_PERFORMANCE: Literal["balanced"] = "balanced"
_DEFAULT_TIMEOUT_SECONDS: int = 30
_DEFAULT_LOG_FILE: str = "logs/piidigger.log"
_DEFAULT_LOG_LEVEL: str = "INFO"
_DEFAULT_RESULTS_PATH: str = "piidigger-results"
_ARCHIVE_MAX_DEPTH: int = 1
_ARCHIVE_MAX_MEMBERS: int = 10_000
_ARCHIVE_MAX_MEMBER_SIZE_MB: int = 512
_ARCHIVE_MAX_TOTAL_SIZE_MB: int = 8192
_DEFAULT_BUFFER_UNIT_BYTES: int = 650
_DEFAULT_BUFFER_UNIT_COUNT: int = 100_000
_DEFAULT_BLANK_ROW_LIMIT: int = 250
_DEFAULT_BLANK_COL_LIMIT: int = 500

_KNOWN_CONFIG_KEYS: tuple[str, ...] = (
    "start_dirs",
    "exclude_dirs",
    "include_exts",
    "include_mime",
    "data_handlers",
    "performance",
    "default_timeout_seconds",
    "local_files_only",
    "admin_check",
    "log_file",
    "log_level",
    "results.path",
    "results.formats",
    "archives.enabled",
    "archives.formats",
    "archives.max_depth",
    "archives.max_members",
    "archives.max_member_uncompressed_size_mb",
    "archives.max_total_uncompressed_size_mb",
    "buffer.buffer_unit_bytes",
    "buffer.buffer_unit_count",
    "spreadsheet.blank_row_limit",
    "spreadsheet.blank_col_limit",
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _default_start_dirs() -> list[Path]:
    system = platform.system().lower()
    if system == "windows":
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return [Path("/")]


def _default_exclude_dirs() -> list[str]:
    system = platform.system().lower()
    if system == "windows":
        return _WINDOWS_EXCLUDE_DIRS
    if system == "darwin":
        return _DARWIN_EXCLUDE_DIRS
    return _LINUX_EXCLUDE_DIRS


def _format_error_location(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc)


def _suggest_config_key(location: str) -> str | None:
    matches = difflib.get_close_matches(location, _KNOWN_CONFIG_KEYS, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _format_validation_errors(path: Path, error: ValidationError) -> str:
    lines = [f"invalid configuration in {path}:"]
    saw_unknown_setting = False

    for item in error.errors(include_url=False):
        location = _format_error_location(item["loc"])
        error_type = item["type"]

        if error_type == "extra_forbidden":
            saw_unknown_setting = True
            message = f"Unknown setting '{location}'."
            suggestion = _suggest_config_key(location)
            if suggestion is not None:
                message += f" Did you mean '{suggestion}'?"
            lines.append(f"- {message}")
            continue

        if error_type == "missing":
            lines.append(f"- Missing required setting '{location}'.")
            continue

        lines.append(f"- Invalid value for '{location}': {item['msg']}.")

    if saw_unknown_setting:
        lines.append("- The configuration file appears to use unsupported or legacy option names.")
        lines.append("- Run 'piidigger config generate' to create a current 2.0 template and copy your values into it.")

    return "\n".join(lines)


def generate_toml_template() -> str:
    """Generate a multi-OS TOML configuration template.

    [start_dirs] and [exclude_dirs] hold per-OS keys so one file works on
    Windows, macOS, and Linux.  from_toml() picks the right key at load time.
    "all" in start_dirs expands to every available drive/mount at scan time.
    """

    def _toml_list(items: list[str]) -> str:
        inner = ", ".join(f'"{item}"' for item in items)
        return f"[{inner}]"

    # Root-level flat keys must come before any [table] headers; a key written
    # after a [header] is parsed as a member of that table by TOML parsers.
    lines: list[str] = [
        'include_exts = ["all"]',
        'include_mime = ["all"]',
        'data_handlers = ["all"]',
        f'performance = "{_DEFAULT_PERFORMANCE}"',
        f"default_timeout_seconds = {_DEFAULT_TIMEOUT_SECONDS}",
        "local_files_only = true",
        "admin_check = true",
        f'log_file = "{_DEFAULT_LOG_FILE}"',
        f'log_level = "{_DEFAULT_LOG_LEVEL}"',
        "",
        "[start_dirs]",
        f"windows = {_toml_list(_WINDOWS_START_DIRS)}",
        f"macos = {_toml_list(_DARWIN_START_DIRS)}",
        f"linux = {_toml_list(_LINUX_START_DIRS)}",
        "",
        "[exclude_dirs]",
        f"windows = {_toml_list(_WINDOWS_EXCLUDE_DIRS)}",
        f"macos = {_toml_list(_DARWIN_EXCLUDE_DIRS)}",
        f"linux = {_toml_list(_LINUX_EXCLUDE_DIRS)}",
        "",
        "[results]",
        f'path = "{_DEFAULT_RESULTS_PATH}"',
        'formats = ["all"]',
        "",
        "[archives]",
        "enabled = true",
        'formats = ["all"]',
        f"max_depth = {_ARCHIVE_MAX_DEPTH}",
        f"max_members = {_ARCHIVE_MAX_MEMBERS}",
        f"max_member_uncompressed_size_mb = {_ARCHIVE_MAX_MEMBER_SIZE_MB}",
        f"max_total_uncompressed_size_mb = {_ARCHIVE_MAX_TOTAL_SIZE_MB}",
        "",
        "[buffer]",
        f"buffer_unit_bytes = {_DEFAULT_BUFFER_UNIT_BYTES}",
        f"buffer_unit_count = {_DEFAULT_BUFFER_UNIT_COUNT}",
        "",
        "[spreadsheet]",
        f"blank_row_limit = {_DEFAULT_BLANK_ROW_LIMIT}",
        f"blank_col_limit = {_DEFAULT_BLANK_COL_LIMIT}",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ArchiveConfig(PiiDiggerModel):
    """Archive scanning configuration.

    Controls archive scanning behaviour.  Defaults enable all registered formats.
    Limits guard against disk exhaustion and runaway scan time.
    Set enabled=False to skip all archive files without any other change.
    """

    enabled: bool = True
    formats: list[str] = Field(default_factory=lambda: ["all"])
    max_depth: int = Field(default=_ARCHIVE_MAX_DEPTH, ge=0, le=3)
    max_members: int = Field(default=_ARCHIVE_MAX_MEMBERS, ge=1)
    max_member_uncompressed_size_mb: int = Field(default=_ARCHIVE_MAX_MEMBER_SIZE_MB, ge=1)
    max_total_uncompressed_size_mb: int = Field(default=_ARCHIVE_MAX_TOTAL_SIZE_MB, ge=1)


class BufferConfig(PiiDiggerModel):
    """RAM-buffering configuration for text-extracting file handlers.

    buffer_unit_bytes * buffer_unit_count is the amount of extracted text
    held in memory before being handed to data handlers.  This bounds
    memory use only — it never causes any part of a file to be skipped.
    """

    buffer_unit_bytes: int = Field(default=_DEFAULT_BUFFER_UNIT_BYTES, ge=1)
    buffer_unit_count: int = Field(default=_DEFAULT_BUFFER_UNIT_COUNT, ge=1)

    @property
    def max_buffer_bytes(self) -> int:
        return self.buffer_unit_bytes * self.buffer_unit_count


class SpreadsheetConfig(PiiDiggerModel):
    """Spreadsheet blank-run cutoff configuration (xls/xlsx handlers).

    Unlike BufferConfig, these settings genuinely skip remaining content:
    a worksheet stops being read once this many consecutive blank rows (or
    a row stops being read once this many consecutive blank columns) are seen.
    """

    blank_row_limit: int = Field(default=_DEFAULT_BLANK_ROW_LIMIT, ge=0)
    blank_col_limit: int = Field(default=_DEFAULT_BLANK_COL_LIMIT, ge=0)


class ResultsConfig(PiiDiggerModel):
    """Output destination and format selection.

    path is the folder where output files are written.
    formats selects which output formats to produce; ["all"] enables every format.
    Valid format names: "csv", "json", "text".
    Filenames are generated automatically: piidigger-<timestamp>.<ext>
    """

    path: Path = Path(_DEFAULT_RESULTS_PATH)
    formats: list[str] = Field(default_factory=lambda: ["all"])


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
    performance: Literal["fast", "balanced", "slow"] = _DEFAULT_PERFORMANCE
    default_timeout_seconds: int = Field(default=_DEFAULT_TIMEOUT_SECONDS, ge=1, le=600)
    local_files_only: bool = True
    admin_check: bool = True
    log_file: Path = Path(_DEFAULT_LOG_FILE)
    log_level: str = _DEFAULT_LOG_LEVEL
    results: ResultsConfig = Field(default_factory=ResultsConfig)
    archives: ArchiveConfig = Field(default_factory=ArchiveConfig)
    buffer: BufferConfig = Field(default_factory=BufferConfig)
    spreadsheet: SpreadsheetConfig = Field(default_factory=SpreadsheetConfig)

    @field_validator("data_handlers")
    @classmethod
    def _validate_data_handlers(cls, v: list[str]) -> list[str]:
        if "all" in v:
            return v
        from piidigger.datahandlers import HANDLER_REGISTRY  # noqa: PLC0415

        unknown = [name for name in v if name not in HANDLER_REGISTRY]
        if unknown:
            known = ", ".join(sorted(HANDLER_REGISTRY))
            raise ValueError(f"unknown data handler(s): {', '.join(unknown)}; known: {known}")
        return v

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

        # Multi-OS format: [start_dirs] / [exclude_dirs] are TOML tables keyed
        # by "windows", "macos", "linux".  Extract the current-OS slice so the
        # rest of validation sees a plain list.
        system = platform.system().lower()
        os_key = "macos" if system == "darwin" else system
        _os_keys = frozenset({"windows", "macos", "linux"})
        for _table in ("start_dirs", "exclude_dirs"):
            _val = data.get(_table)
            if not isinstance(_val, dict):
                continue
            _stray = [k for k in _val if k not in _os_keys]
            if _stray:
                # Keys that aren't OS names ended up here because flat root-level
                # settings were written after a [section] header in the TOML file.
                # TOML absorbs them into that section; they would be silently lost.
                raise ValueError(
                    f"invalid configuration in {path}:\n"
                    f"- Unexpected keys found inside [{_table}]: {', '.join(sorted(_stray))}.\n"
                    f"  Root-level settings must appear before any [section] header in the TOML file.\n"
                    f"  Run 'piidigger config generate' to create a correctly structured template."
                )
            data[_table] = _val.get(os_key, [])

        # "all" expands to every available drive/mount point on the current OS.
        if data.get("start_dirs") == ["all"]:
            data["start_dirs"] = [str(d) for d in _default_start_dirs()]

        try:
            config = cls.model_validate(data)
        except ValidationError as e:
            raise ValueError(_format_validation_errors(path, e)) from e

        for d in config.start_dirs:
            if not d.exists():
                raise ValueError(f"start directory does not exist: {d}")

        try:
            config.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ValueError(f"cannot create log directory {config.log_file.parent}: {e}") from e

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
