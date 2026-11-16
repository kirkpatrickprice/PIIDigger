"""Unit tests for orchestration/worker/_not_content.py: macOS metadata and Office lock files."""

from __future__ import annotations

from pathlib import Path

import pytest

from piidigger.orchestration.worker._not_content import (
    APPLEDOUBLE,
    OFFICE_LOCK_FILE,
    appledouble_original,
    is_appledouble,
    is_office_lock_file,
    not_content_reason,
)
from tests._not_content_samples import appledouble_bytes, office_lock_bytes, xlsx_bytes


@pytest.mark.unit
def test_appledouble_needs_both_the_name_and_the_magic() -> None:
    head = appledouble_bytes()[:8]

    assert is_appledouble("._report.pdf", head)
    assert not is_appledouble("report.pdf", head), "the magic alone is not enough"
    assert not is_appledouble("._notes.txt", b"card 4111"), "a user's file named ._ is content"
    assert not is_appledouble("._report.pdf", b"\x00\x05\x16\x00"), "AppleSingle is not AppleDouble"


@pytest.mark.unit
@pytest.mark.parametrize("name", ["~$report.docx", "~$Budget.XLSX", "~$deck.pptx", "~$old.doc", "~$old.xls"])
def test_office_lock_file_by_name_and_missing_signature(name: str) -> None:
    assert is_office_lock_file(name, office_lock_bytes()[:8])


@pytest.mark.unit
@pytest.mark.parametrize("head", [b"PK\x03\x04\x14\x00\x06\x00", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"])
def test_a_real_document_named_like_a_lock_file_is_content(head: bytes) -> None:
    assert not is_office_lock_file("~$report.docx", head)


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [
        b'<html xmlns:o="urn:schemas-microsoft-com:office:office">\r\n<body>4111111111111111</body></html>\r\n',
        b'<?xml version="1.0"?>\r\n<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"/>\r\n',
        b"{\\rtf1\\ansi\\deff0\r\n{\\fonttbl{\\f0 Calibri;}}\r\n4111111111111111}\r\n",
        bytes([5]) + b"randy" + b" " * 2000,
        bytes([40]) + b"randy",
        bytes([0]) + b"randy",
        b"",
        b"Name,SSN,Card,Expires,Address,City,State,Zip,Phone,Email,Manager,Department,Cost center",
        b"<table><tr><td>Jane Doe</td><td>4111111111111111</td></tr></table>",
        b"1,Jane Doe,4111111111111111,12/29,1 Main Street,Springfield,IL,62701",
        bytes([5]) + b"randyX" + b" " * 40,
        bytes([60]) + b"x" * 60 + b" " * 40,
    ],
    ids=[
        "html-export",
        "spreadsheetml",
        "rtf",
        "too-large",
        "name-cut-short",
        "zero-length",
        "empty",
        "one-line-csv",
        "one-line-html",
        "csv-starting-with-a-digit",
        "no-padding-after-the-name",
        "length-past-the-name-field",
    ],
)
def test_a_file_without_a_lock_files_shape_is_content(content: bytes) -> None:
    """.xls and .doc files are often HTML, XML or RTF, with no zip or OLE signature, so the shape must match."""
    assert not is_office_lock_file("~$export.xls", content)


@pytest.mark.unit
def test_a_lock_file_may_hold_a_name_outside_ascii() -> None:
    """The first copy of the name is in the user's ANSI code page."""
    name = "José".encode("cp1252")
    assert is_office_lock_file("~$report.docx", bytes([len(name)]) + name + b" " * 40)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["~$notes.txt", "~$data.csv", "report.docx", "$report.docx"])
def test_office_lock_file_needs_the_prefix_and_an_office_extension(name: str) -> None:
    assert not is_office_lock_file(name, office_lock_bytes()[:8])


@pytest.mark.unit
def test_not_content_reason_reads_the_file(tmp_path: Path) -> None:
    samples = {
        "._report.pdf": (appledouble_bytes(), APPLEDOUBLE),
        "~$report.docx": (office_lock_bytes(), OFFICE_LOCK_FILE),
        "._notes.txt": (b"card 4111111111111111\n", None),
        "~$x.xlsx": (xlsx_bytes("hello"), None),
    }
    for name, (content, _) in samples.items():
        (tmp_path / name).write_bytes(content)

    assert {name: not_content_reason(tmp_path / name, name) for name in samples} == {
        name: reason for name, (_, reason) in samples.items()
    }


@pytest.mark.unit
def test_not_content_reason_goes_by_the_name_it_is_given(tmp_path: Path) -> None:
    """An extracted archive member has a generated name; the member's own name decides."""
    extracted = tmp_path / "0.docx"
    extracted.write_bytes(office_lock_bytes())

    assert not_content_reason(extracted, extracted.name) is None
    assert not_content_reason(extracted, "~$report.docx") == OFFICE_LOCK_FILE


@pytest.mark.unit
def test_not_content_reason_opens_no_file_whose_name_does_not_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("opened a file whose name cannot match")

    monkeypatch.setattr(Path, "open", no_open)

    assert not_content_reason(tmp_path / "report.docx", "report.docx") is None


@pytest.mark.unit
def test_a_file_that_cannot_be_read_is_left_to_the_scan(tmp_path: Path) -> None:
    assert not_content_reason(tmp_path / "missing", "~$report.docx") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "original"),
    [
        ("._a.txt", "a.txt"),
        ("docs/._a.txt", "docs/a.txt"),
        ("__MACOSX/docs/._a.txt", "docs/a.txt"),
        ("__MACOSX/._a.txt", "a.txt"),
        ("docs/__MACOSX/._a.txt", "docs/__MACOSX/a.txt"),
        ("a.txt", None),
        ("._", None),
        ("docs/a._b.txt", None),
    ],
)
def test_appledouble_original_names_the_file_it_would_describe(name: str, original: str | None) -> None:
    """Only a top-level __MACOSX folder is Finder's; one deeper is a user's folder."""
    assert appledouble_original(name) == original
