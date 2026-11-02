"""Unit tests for the Click CLI entry point."""

from __future__ import annotations

import os
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
def test_root_version_comes_from_the_package_not_its_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PyInstaller builds ship no dist-info, so importlib.metadata cannot be relied on."""
    import importlib
    import importlib.metadata

    import piidigger
    import piidigger.cli.main

    def no_metadata(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", no_metadata)
    reloaded = importlib.reload(piidigger.cli.main)
    try:
        result = CliRunner().invoke(reloaded.cli, ["--version"])
    finally:
        monkeypatch.undo()
        importlib.reload(piidigger.cli.main)

    assert result.exit_code == 0
    assert f"PIIDigger version: {piidigger.__version__}" in result.output


@pytest.mark.unit
def test_inspect_help_exits_0() -> None:
    result = CliRunner().invoke(cli, ["inspect", "--help"])
    assert result.exit_code == 0
    assert "datatypes" in result.output
    assert "filetypes" in result.output
    assert "file" in result.output
    assert "mime" not in result.output
    assert "encoding" not in result.output.replace("encoding and outcome", "")


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


def _default_config(tmp_path: Path) -> list[str]:
    """Pin the defaults, so a piidigger.toml in the directory the tests run from can't change the outcome."""
    config = tmp_path / "defaults.toml"
    config.write_text("", encoding="utf-8")
    return ["-f", str(config)]


def _inspect_file(tmp_path: Path, name: str, content: bytes, *args: str) -> str:
    fixture = tmp_path / name
    fixture.write_bytes(content)

    result = CliRunner().invoke(cli, ["inspect", "file", str(fixture), *args])

    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.unit
def test_inspect_file_reports_text_and_matches_scanner_encoding_for_ascii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # inspect reports utf_8 for pure ASCII, the encoding the plaintext handler reads it with.
    monkeypatch.chdir(tmp_path)
    output = _inspect_file(tmp_path, "sample.txt", b"hello a@b.com\n")

    assert "Extension: .txt" in output
    assert "MIME type: text/plain" in output
    assert "Encoding:  utf_8" in output
    assert "Config:    built-in defaults" in output
    assert "Outcome:   Scanned with PlaintextHandler" in output
    assert "Note:" not in output


@pytest.mark.unit
def test_inspect_file_reports_a_real_pdf(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["inspect", "file", "testdata/pan/sample-pans.pdf", *_default_config(tmp_path)])

    assert result.exit_code == 0
    assert "MIME type: application/pdf" in result.output
    assert "Encoding:  (not used)" in result.output
    assert "Outcome:   Scanned with PdfHandler" in result.output


@pytest.mark.unit
def test_inspect_file_explains_text_that_sniffs_as_pdf(tmp_path: Path) -> None:
    output = _inspect_file(
        tmp_path, "customers.csv", b"%PDF-1.4,name\nalice@example.com,Alice\n", *_default_config(tmp_path)
    )

    assert "Outcome:   Scanned with PlaintextHandler" in output
    assert "Note:      content looks like application/pdf" in output


@pytest.mark.unit
def test_inspect_file_real_pdf_named_txt_says_it_will_not_be_read(tmp_path: Path) -> None:
    output = _inspect_file(
        tmp_path, "report.txt", Path("testdata/pan/sample-pans.pdf").read_bytes(), *_default_config(tmp_path)
    )

    assert "Outcome:   Not read: text encoding could not be detected" in output


@pytest.mark.unit
def test_inspect_file_reports_an_archive(tmp_path: Path) -> None:
    assert "Outcome:   Scanned: members are read from this zip archive" in _inspect_file(
        tmp_path, "a.zip", b"PK\x05\x06", *_default_config(tmp_path)
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "content", "toml", "reason"),
    [
        ("a.txt", b"hello\n", 'include_exts = [".pdf"]\n', "include_exts does not list .txt"),
        ("A.TXT", b"hello\n", 'include_exts = [".pdf"]\n', "include_exts does not list .txt"),
        ("a.bak", b"hello a@b.com\n", "include_mime = []\n", "include_mime does not list text/plain"),
        ("a.png", b"\x89PNG\r\n\x1a\n" + bytes(32), "", "no file handler reads image/png"),
    ],
)
def test_inspect_file_names_why_a_file_is_not_scanned_under_a_config(
    tmp_path: Path, name: str, content: bytes, toml: str, reason: str
) -> None:
    config = tmp_path / "c.toml"
    config.write_text(toml, encoding="utf-8")

    output = _inspect_file(tmp_path, name, content, "-f", str(config))

    assert f"Config:    {config}" in output
    assert "Outcome:   Not scanned:" in output
    assert reason in output


@pytest.mark.unit
def test_inspect_file_picks_up_piidigger_toml_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "piidigger.toml").write_text('include_exts = [".pdf"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert "Not scanned" in _inspect_file(tmp_path, "a.txt", b"hello\n")


@pytest.mark.unit
def test_inspect_file_invalid_config_is_an_error(tmp_path: Path) -> None:
    fixture = tmp_path / "a.txt"
    fixture.write_text("hello\n", encoding="utf-8")
    config = tmp_path / "c.toml"
    config.write_text('include_exts = [".nope"]\n', encoding="utf-8")

    result = CliRunner().invoke(cli, ["inspect", "file", str(fixture), "-f", str(config)])

    assert result.exit_code != 0
    assert "unknown extension" in result.output


@pytest.mark.unit
@pytest.mark.parametrize(
    ("toml", "reason"),
    [
        ("[archives]\nenabled = false\n", "archives.enabled is false"),
        ('[archives]\nformats = ["7z"]\n', "archives.formats does not list zip"),
    ],
)
def test_inspect_file_names_the_setting_that_turns_an_archive_away(tmp_path: Path, toml: str, reason: str) -> None:
    config = tmp_path / "c.toml"
    config.write_text(toml, encoding="utf-8")

    output = _inspect_file(tmp_path, "a.zip", b"PK\x05\x06", "-f", str(config))

    assert f"Outcome:   Not scanned: {reason}" in output


@pytest.mark.unit
def test_inspect_file_says_a_symlink_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "target.csv"
    target.write_text("a,b\n", encoding="utf-8")
    link = tmp_path / "link.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not available")

    result = CliRunner().invoke(cli, ["inspect", "file", str(link), *_default_config(tmp_path)])

    assert result.exit_code == 0
    assert "Outcome:   Not scanned: symbolic links are skipped" in result.output


@pytest.mark.unit
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_inspect_file_does_not_open_a_fifo(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.txt"
    os.mkfifo(fifo)

    result = CliRunner().invoke(cli, ["inspect", "file", str(fifo), *_default_config(tmp_path)])

    assert result.exit_code == 0
    assert "Outcome:   Not scanned: it is not a regular file" in result.output


@pytest.mark.unit
def test_inspect_file_config_path_that_is_a_folder_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = tmp_path / "a.txt"
    fixture.write_text("hello\n", encoding="utf-8")
    (tmp_path / "piidigger.toml").mkdir()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["inspect", "file", str(fixture)])

    assert result.exit_code == 1
    assert "Error:" in result.output


@pytest.mark.unit
def test_inspect_file_empty_text_file_is_scanned(tmp_path: Path) -> None:
    output = _inspect_file(tmp_path, "empty.txt", b"", *_default_config(tmp_path))

    assert "Outcome:   Scanned with PlaintextHandler" in output


@pytest.mark.unit
def test_inspect_file_rejects_a_folder(tmp_path: Path) -> None:
    assert CliRunner().invoke(cli, ["inspect", "file", str(tmp_path)]).exit_code != 0


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
