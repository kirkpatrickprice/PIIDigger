"""Unit tests for coordinator module-level helper functions.

These functions live in coordinator.py but are directly importable for isolated
unit testing without spawning any processes.
"""

from __future__ import annotations

import pytest

from piidigger.orchestration.coordinator import (
    _denied_path,
    _findings_summary,
    _is_access_denied,
    _short_error,
    _truncate_path,
)

# ---------------------------------------------------------------------------
# _truncate_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_truncate_path_short_path_unchanged() -> None:
    path = "C:/short/path.txt"
    assert _truncate_path(path) == path


@pytest.mark.unit
def test_truncate_path_exactly_max_unchanged() -> None:
    path = "a" * 60
    assert _truncate_path(path) == path


@pytest.mark.unit
def test_truncate_path_long_path_with_backslash() -> None:
    path = "C:\\Users\\Randy\\Documents\\very_long_folder_name_here\\subfolder\\filename.txt"
    result = _truncate_path(path, max_len=60)
    assert len(result) <= 60
    assert "filename.txt" in result
    assert "..." in result


@pytest.mark.unit
def test_truncate_path_long_path_with_forward_slash() -> None:
    path = "/home/randy/very_long_folder/another_long_folder/afile.txt"
    result = _truncate_path(path, max_len=40)
    assert len(result) <= 40
    assert "afile.txt" in result
    assert "..." in result


@pytest.mark.unit
def test_truncate_path_no_separator_falls_back() -> None:
    path = "a" * 80
    result = _truncate_path(path, max_len=60)
    assert len(result) == 60
    assert result.endswith("...")


# ---------------------------------------------------------------------------
# _is_access_denied
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "msg",
    [
        "Access is denied: 'C:\\secret'",
        "Permission denied: '/etc/shadow'",
        "[WinError 5] Access is denied",
        "[Errno 13] Permission denied",
    ],
)
def test_is_access_denied_true(msg: str) -> None:
    assert _is_access_denied(msg)


@pytest.mark.unit
def test_is_access_denied_false() -> None:
    assert not _is_access_denied("FileNotFoundError: No such file or directory")


# ---------------------------------------------------------------------------
# _denied_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_denied_path_extracts_path() -> None:
    msg = "[Errno 13] Permission denied: '/etc/shadow'"
    assert _denied_path(msg) == "/etc/shadow"


@pytest.mark.unit
def test_denied_path_windows_style() -> None:
    msg = "[WinError 5] Access is denied: 'C:\\Windows\\System32\\protected'"
    assert _denied_path(msg) == "C:\\Windows\\System32\\protected"


@pytest.mark.unit
def test_denied_path_no_colon_quote_separator() -> None:
    msg = "just some error without a path"
    assert _denied_path(msg) == "just some error without a path"


# ---------------------------------------------------------------------------
# _short_error
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_short_error_strips_path_suffix() -> None:
    msg = "[Errno 13] Permission denied: '/etc/shadow'"
    assert _short_error(msg) == "[Errno 13] Permission denied"


@pytest.mark.unit
def test_short_error_truncates_at_80() -> None:
    msg = "x" * 100
    result = _short_error(msg)
    assert len(result) == 80


@pytest.mark.unit
def test_short_error_uses_first_line_only() -> None:
    msg = "first line\nsecond line\nthird line"
    assert _short_error(msg) == "first line"


@pytest.mark.unit
def test_short_error_short_message_unchanged() -> None:
    msg = "File not found"
    assert _short_error(msg) == "File not found"


# ---------------------------------------------------------------------------
# _findings_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_findings_summary_empty_list() -> None:
    assert _findings_summary([]) == ""


@pytest.mark.unit
def test_findings_summary_single_finding() -> None:
    findings = [
        {
            "source_path": "testdata/pan/sample.txt",
            "handler": "pan",
            "matches": {"pan": ["4111111111111111"]},
        }
    ]
    result = _findings_summary(findings)
    assert "sample.txt" in result
    assert "PAN: 1" in result


@pytest.mark.unit
def test_findings_summary_multiple_handlers_sorted() -> None:
    findings = [
        {
            "source_path": "file.txt",
            "handler": "pan",
            "matches": {"pan": ["4111111111111111", "5555555555554444"]},
        },
        {
            "source_path": "file.txt",
            "handler": "ssn",
            "matches": {"ssn": ["123-45-6789"]},
        },
    ]
    result = _findings_summary(findings)
    # Counts are summed per handler, output is alphabetically sorted
    assert "PAN: 2" in result
    assert "SSN: 1" in result
    assert result.index("PAN") < result.index("SSN")


@pytest.mark.unit
def test_findings_summary_missing_keys_use_defaults() -> None:
    # Findings with no 'handler' or 'matches' should not raise
    findings = [{"source_path": "file.txt"}]
    result = _findings_summary(findings)
    assert "file.txt" in result
    assert "?: 0" in result
