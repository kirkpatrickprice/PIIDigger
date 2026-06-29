"""Tests for the Phase 3 Config Pydantic model."""

from __future__ import annotations

from pathlib import Path

import pytest

from piidigger.models.config import Config, ResultsConfig, generate_toml_template

# ---------------------------------------------------------------------------
# ResultsConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_results_config_defaults() -> None:
    r = ResultsConfig()
    assert r.path == Path("piidigger-results")
    assert r.formats == ["all"]


@pytest.mark.unit
def test_results_config_custom(tmp_path: Path) -> None:
    r = ResultsConfig(path=tmp_path / "output", formats=["csv", "json"])
    assert r.path == tmp_path / "output"
    assert r.formats == ["csv", "json"]


@pytest.mark.unit
def test_results_config_rejects_unknown_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ResultsConfig(unknown_field="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_default_returns_config() -> None:
    c = Config.default()
    assert isinstance(c, Config)
    assert len(c.start_dirs) >= 1
    assert len(c.exclude_dirs) >= 1
    assert c.performance == "balanced"
    assert c.local_files_only is True


@pytest.mark.unit
def test_config_default_is_picklable() -> None:
    import pickle

    c = Config.default()
    restored: Config = pickle.loads(pickle.dumps(c))
    assert restored.start_dirs == c.start_dirs
    assert restored.performance == c.performance


# ---------------------------------------------------------------------------
# Config.from_toml — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_toml_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        Config.from_toml(tmp_path / "does_not_exist.toml")


@pytest.mark.unit
def test_from_toml_invalid_toml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not valid toml [[[", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid TOML"):
        Config.from_toml(bad)


@pytest.mark.unit
def test_from_toml_unknown_field_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(
        f'start_dirs = ["{tmp_path.as_posix()}"]\nunknown_option = true\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unknown setting 'unknown_option'"):
        Config.from_toml(bad)


@pytest.mark.unit
def test_from_toml_unknown_field_suggests_current_template(tmp_path: Path) -> None:
    bad = tmp_path / "legacy.toml"
    bad.write_text(
        f'start_dirs = ["{tmp_path.as_posix()}"]\ndataHandlers = ["pan"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Did you mean 'data_handlers'\\?") as exc_info:
        Config.from_toml(bad)

    assert "piidigger config generate" in str(exc_info.value)


@pytest.mark.unit
def test_from_toml_multi_os_format(tmp_path: Path) -> None:
    """from_toml() extracts the current OS slice from a multi-OS config."""
    toml = tmp_path / "multi.toml"
    # All three OS keys point at tmp_path so the existence check passes on
    # whichever OS runs this test.
    toml.write_text(
        f'[start_dirs]\nwindows = ["{tmp_path.as_posix()}"]\n'
        f'macos = ["{tmp_path.as_posix()}"]\n'
        f'linux = ["{tmp_path.as_posix()}"]\n'
        f"\n[exclude_dirs]\nwindows = []\nmacos = []\nlinux = []\n",
        encoding="utf-8",
    )

    config = Config.from_toml(toml)
    assert config.start_dirs == [tmp_path]
    assert config.exclude_dirs == []


@pytest.mark.unit
def test_generate_toml_template_is_valid_toml() -> None:
    """generate_toml_template() produces TOML that tomllib can parse."""
    import tomllib

    text = generate_toml_template()
    data = tomllib.loads(text)
    assert isinstance(data["start_dirs"], dict)
    assert "windows" in data["start_dirs"]
    assert "macos" in data["start_dirs"]
    assert "linux" in data["start_dirs"]
    assert isinstance(data["exclude_dirs"], dict)


@pytest.mark.unit
def test_from_toml_missing_start_dir(tmp_path: Path) -> None:
    toml = tmp_path / "cfg.toml"
    toml.write_text(
        'start_dirs = ["/nonexistent/path/that/should/not/exist/xyz123"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="start directory does not exist"):
        Config.from_toml(toml)
