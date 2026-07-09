"""End-to-end integration tests: run_scan() against real testdata.

Marked @pytest.mark.e2e — run with:  uv run pytest tests/ -m e2e -v
These tests spin up real worker processes and write real output files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piidigger.models.config import Config, ResultsConfig
from piidigger.run import run_scan


def _config(start_dir: Path, tmp_path: Path, **kwargs: object) -> Config:
    """Build a minimal Config suitable for a test scan."""
    return Config(
        start_dirs=[start_dir],
        performance="slow",
        log_file=tmp_path / "logs" / "test.log",
        results=ResultsConfig(path=tmp_path / "output", **kwargs),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# testdata/plaintext/ — no PII expected; verifies scan completes cleanly
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_run_scan_plaintext_creates_csv(tmp_path: Path) -> None:
    """run_scan() on testdata/plaintext/ exits 0 and creates a CSV file."""
    start = Path("testdata/plaintext")
    if not start.exists():
        pytest.skip("testdata not available")

    exit_code = run_scan(_config(start, tmp_path, formats=["csv"]))

    assert exit_code == 0
    csv_files = list((tmp_path / "output").glob("*.csv"))
    assert len(csv_files) == 1


# ---------------------------------------------------------------------------
# testdata/pan/ — known PII; verifies all three output formats are non-empty
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_run_scan_pan_all_formats_non_empty(tmp_path: Path) -> None:
    """run_scan() on testdata/pan/ creates all three formats and each has findings."""
    start = Path("testdata/pan")
    if not start.exists():
        pytest.skip("testdata not available")

    config = Config(
        start_dirs=[start],
        performance="slow",
        data_handlers=["pan"],
        log_file=tmp_path / "logs" / "test.log",
        results=ResultsConfig(path=tmp_path / "output", formats=["all"]),
    )
    exit_code = run_scan(config)

    assert exit_code == 0
    out = tmp_path / "output"
    # Each format produces at least one file; JSON also writes a .jsonl streaming file.
    for ext in (".csv", ".json", ".txt"):
        matches = list(out.glob(f"*{ext}"))
        assert len(matches) == 1, f"expected exactly one {ext} file, got {matches}"
        assert matches[0].stat().st_size > 0, f"{matches[0].name} is empty — expected PII findings"
