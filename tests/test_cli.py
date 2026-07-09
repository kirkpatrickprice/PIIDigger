"""Unit tests for the Click CLI entry point."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from piidigger.cli.main import cli


@pytest.mark.unit
def test_root_help_exits_0() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
    assert "config" in result.output
    assert "inspect" in result.output


@pytest.mark.unit
def test_scan_help_exits_0() -> None:
    result = CliRunner().invoke(cli, ["scan", "--help"])
    assert result.exit_code == 0
    assert "Scan directories for PII" in result.output
    assert "--performance" not in result.output
    assert "--list-datahandlers" not in result.output


@pytest.mark.unit
def test_config_help_exits_0() -> None:
    result = CliRunner().invoke(cli, ["config", "--help"])
    assert result.exit_code == 0
    assert "generate" in result.output
    assert "validate" in result.output


@pytest.mark.unit
def test_root_version_exits_0() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "PIIDigger version:" in result.output


@pytest.mark.unit
def test_inspect_help_exits_0() -> None:
    result = CliRunner().invoke(cli, ["inspect", "--help"])
    assert result.exit_code == 0
    assert "datatypes" in result.output
    assert "filetypes" in result.output
    assert "mime" in result.output
    assert "encoding" in result.output


@pytest.mark.unit
def test_inspect_datatypes_exits_0() -> None:
    result = CliRunner().invoke(cli, ["inspect", "datatypes"])
    assert result.exit_code == 0
    assert "pan" in result.output


@pytest.mark.unit
def test_inspect_filetypes_exits_0() -> None:
    result = CliRunner().invoke(cli, ["inspect", "filetypes"])
    assert result.exit_code == 0
    assert ".txt" in result.output


@pytest.mark.unit
def test_inspect_cpu_count_exits_0() -> None:
    result = CliRunner().invoke(cli, ["inspect", "cpu"])
    assert result.exit_code == 0
    assert "Physical CPUs" in result.output
    assert "Logical CPUs" in result.output


@pytest.mark.unit
def test_inspect_mime_exits_0(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fixture = tmp_path / "sample.txt"
    fixture.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("piidigger.cli.commands.inspect.get_mime", lambda _: "text/plain")

    result = CliRunner().invoke(cli, ["inspect", "mime", str(fixture)])

    assert result.exit_code == 0
    assert "text/plain" in result.output


@pytest.mark.unit
def test_inspect_encoding_exits_0(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fixture = tmp_path / "sample.txt"
    fixture.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("piidigger.cli.commands.inspect.detect_encoding", lambda _: "utf-8")

    result = CliRunner().invoke(cli, ["inspect", "encoding", str(fixture)])

    assert result.exit_code == 0
    assert "utf-8" in result.output



@pytest.mark.unit
def test_config_generate_creates_file(tmp_path: Path) -> None:
    dest = str(tmp_path / "out.toml")
    result = CliRunner().invoke(cli, ["config", "generate", dest])
    assert result.exit_code == 0
    assert Path(dest).exists()


@pytest.mark.unit
def test_config_validate_valid_file_exits_0(tmp_path: Path) -> None:
    toml = tmp_path / "cfg.toml"
    toml.write_text(f'start_dirs = ["{tmp_path.as_posix()}"]\n', encoding="utf-8")
    result = CliRunner().invoke(cli, ["config", "validate", str(toml)])
    assert result.exit_code == 0
    assert "OK" in result.output


@pytest.mark.unit
def test_config_validate_missing_file_exits_nonzero(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["config", "validate", str(tmp_path / "missing.toml")])
    assert result.exit_code != 0


@pytest.mark.unit
def test_config_validate_invalid_file_shows_friendly_error(tmp_path: Path) -> None:
    toml = tmp_path / "legacy.toml"
    toml.write_text(
        f'start_dirs = ["{tmp_path.as_posix()}"]\ndataHandlers = ["pan", "email"]\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["config", "validate", str(toml)])

    assert result.exit_code != 0
    assert "Unknown setting 'dataHandlers'." in result.output
    assert "Did you mean 'data_handlers'?" in result.output
    assert "piidigger config generate" in result.output
    assert "validation errors for Config" not in result.output


# ---------------------------------------------------------------------------
# scan -f / auto-detect piidigger.toml
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_f_flag_is_recognized() -> None:
    """-f short flag and --config long flag both appear in scan help."""
    result = CliRunner().invoke(cli, ["scan", "--help"])
    assert result.exit_code == 0
    assert "-f" in result.output
    assert "--config" in result.output


@pytest.mark.unit
def test_scan_auto_detects_piidigger_toml_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No -f flag: piidigger.toml found in CWD is loaded silently (no 'not found' message)."""
    toml = tmp_path / "piidigger.toml"
    toml.write_text(f'start_dirs = ["{tmp_path.as_posix()}"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("piidigger.cli.commands.scan.run_scan", lambda _: 0)

    result = CliRunner().invoke(cli, ["scan"])

    assert result.exit_code == 0
    assert "not found" not in result.output


@pytest.mark.unit
def test_scan_uses_defaults_silently_when_no_toml_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No -f flag and no piidigger.toml in CWD: built-in defaults used, no warning emitted."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("piidigger.cli.commands.scan.run_scan", lambda _: 0)

    result = CliRunner().invoke(cli, ["scan"])

    assert result.exit_code == 0
    assert "not found" not in result.output


@pytest.mark.unit
def test_scan_f_flag_errors_on_missing_file(tmp_path: Path) -> None:
    """Explicit -f with a non-existent path exits non-zero with an error message."""
    result = CliRunner().invoke(cli, ["scan", "-f", str(tmp_path / "missing.toml")])
    assert result.exit_code != 0
    assert "Error" in result.output
