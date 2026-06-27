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


@pytest.mark.unit
def test_scan_help_exits_0() -> None:
    result = CliRunner().invoke(cli, ["scan", "--help"])
    assert result.exit_code == 0
    assert "Scan directories for PII" in result.output


@pytest.mark.unit
def test_config_help_exits_0() -> None:
    result = CliRunner().invoke(cli, ["config", "--help"])
    assert result.exit_code == 0
    assert "generate" in result.output
    assert "validate" in result.output


@pytest.mark.unit
def test_scan_list_datahandlers_exits_0() -> None:
    result = CliRunner().invoke(cli, ["scan", "--list-datahandlers"])
    assert result.exit_code == 0
    assert "pan" in result.output


@pytest.mark.unit
def test_scan_list_filetypes_exits_0() -> None:
    result = CliRunner().invoke(cli, ["scan", "--list-filetypes"])
    assert result.exit_code == 0
    assert ".txt" in result.output


@pytest.mark.unit
def test_scan_cpu_count_exits_0() -> None:
    result = CliRunner().invoke(cli, ["scan", "--cpu-count"])
    assert result.exit_code == 0
    assert "Logical CPUs" in result.output


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
