"""Tests for the Phase 3 Config Pydantic model."""

from __future__ import annotations

from pathlib import Path

import pytest

from piidigger.models.config import Config, ResultsConfig

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
    assert c.max_workers >= 1
    assert c.local_files_only is True


@pytest.mark.unit
def test_config_default_is_picklable() -> None:
    import pickle
    c = Config.default()
    restored: Config = pickle.loads(pickle.dumps(c))
    assert restored.start_dirs == c.start_dirs
    assert restored.max_workers == c.max_workers


@pytest.mark.unit
def test_config_to_toml_str_roundtrip(tmp_path: Path) -> None:
    """to_toml_str() produces valid TOML that from_toml() can re-read."""
    c = Config(
        start_dirs=[tmp_path],
        data_handlers=["pan"],
        max_workers=2,
    )
    toml_text = c.to_toml_str()
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(toml_text, encoding="utf-8")
    restored = Config.from_toml(toml_file)
    assert restored.start_dirs == [tmp_path]
    assert restored.data_handlers == ["pan"]
    assert restored.max_workers == 2


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
    with pytest.raises(ValueError, match="invalid configuration"):
        Config.from_toml(bad)


@pytest.mark.unit
def test_from_toml_missing_start_dir(tmp_path: Path) -> None:
    toml = tmp_path / "cfg.toml"
    toml.write_text(
        'start_dirs = ["/nonexistent/path/that/should/not/exist/xyz123"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="start directory does not exist"):
        Config.from_toml(toml)
