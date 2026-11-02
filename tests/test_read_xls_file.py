from pathlib import Path
from typing import Any

import pytest

import piidigger.filehandlers.xls as xls_mod
from piidigger.filehandlers.xls import XlsHandler
from piidigger.models.config import Config, SpreadsheetConfig
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path, config: Config | None = None) -> list[str]:
    return list(XlsHandler().read(FilesystemItem(path), config or Config()))


@pytest.mark.filehandlers
def test_xls_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/xls/does-not-exist.xls"))


@pytest.mark.filehandlers
def test_xls_empty_file() -> None:
    chunks = _read(Path("testdata/xls/empty-file.xls"))
    assert chunks == []


# Small, predictable fixtures: exact per-sheet chunk assertions.
# XlsHandler yields one chunk per sheet; with the default buffer size each sheet's
# entire content fits in a single chunk.
@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "filename, expected_chunks",
    [
        (
            "testdata/xls/test-1sheet-1cell.xls",
            ["S1R1C1"],
        ),
        (
            "testdata/xls/test-1sheet-1row.xls",
            ["S1R1C1 S1R1C2 S1R1C3 S1R1C4"],
        ),
        (
            "testdata/xls/test-1sheet-10row-table.xls",
            [
                "Sheet Row Column Text 1 1 3 S1R1C3 1 2 3 S1R2C3 1 3 3 S1R3C3 1 4 3 S1R4C3 1 5 3 S1R5C3 1 6 3 S1R6C3 1 7 3 S1R7C3 1 8 3 S1R8C3 1 9 3 S1R9C3 1 10 3 S1R10C3"
            ],
        ),
        (
            "testdata/xls/test-2sheet-10row-table.xls",
            [
                "Sheet Row Column Text 1 1 3 S1R1C3 1 2 3 S1R2C3 1 3 3 S1R3C3 1 4 3 S1R4C3 1 5 3 S1R5C3 1 6 3 S1R6C3 1 7 3 S1R7C3 1 8 3 S1R8C3 1 9 3 S1R9C3 1 10 3 S1R10C3",
                "Sheet Row Column Text 1 1 3 S1R1C3 1 2 3 S1R2C3 1 3 3 S1R3C3 1 4 3 S1R4C3 1 5 3 S1R5C3 1 6 3 S1R6C3 1 7 3 S1R7C3 1 8 3 S1R8C3 1 9 3 S1R9C3 1 10 3 S1R10C3",
            ],
        ),
    ],
)
def test_xls_exact_content(filename: str, expected_chunks: list[str]) -> None:
    chunks = _read(Path(filename))
    assert chunks == expected_chunks


@pytest.mark.filehandlers
def test_xls_random_data_table() -> None:
    # Large table that was split into 22 chunks with maxChunkCount=2; now
    # arrives as a single chunk with the default buffer size.
    chunks = _read(Path("testdata/xls/random-data-table.xls"))
    content = " ".join(chunks)
    assert "First Name" in content
    assert "j.montgomery@randatmail.com" in content
    assert "Lower secondary" in content


class _FakeSheet:
    def __init__(self, rows: list[list[Any]]) -> None:
        self._rows = rows
        self.nrows = len(rows)
        self.ncols = max(len(r) for r in rows)

    def cell_value(self, row: int, col: int) -> Any:
        return self._rows[row][col] if col < len(self._rows[row]) else ""


class _FakeBook:
    def __init__(self, sheet: _FakeSheet) -> None:
        self._sheet = sheet

    def sheet_names(self) -> list[str]:
        return ["Sheet1"]

    def sheet_by_name(self, _name: str) -> _FakeSheet:
        return self._sheet

    def unload_sheet(self, _name: str) -> None:
        pass

    def release_resources(self) -> None:
        pass


@pytest.mark.filehandlers
def test_xls_blank_col_limit_counts_consecutive_blanks_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scattered blanks must not add up to the limit; see the xlsx test of the same name.

    xlrd cannot write .xls files, so the workbook is faked.
    """
    row = ["A", "", "", "B", "", "", "C", "", "", "4111111111111111"]
    monkeypatch.setattr(xls_mod.xlrd, "open_workbook", lambda *_a, **_k: _FakeBook(_FakeSheet([row])))
    path = tmp_path / "fake.xls"
    path.write_bytes(b"")

    content = " ".join(_read(path, Config(spreadsheet=SpreadsheetConfig(blank_col_limit=2))))

    assert content.split() == ["A", "B", "C", "4111111111111111"]


@pytest.mark.filehandlers
def test_xls_zero_bytes_is_not_reported(tmp_path: Path) -> None:
    path = tmp_path / "zero.xls"
    path.write_bytes(b"")
    item = FilesystemItem(path)

    assert list(XlsHandler().read(item, Config())) == []
    assert item.unread_reason is None
    assert not item.nothing_read


@pytest.mark.filehandlers
def test_xls_not_a_workbook_is_reported_in_the_shared_wording(tmp_path: Path) -> None:
    path = tmp_path / "bad.xls"
    path.write_bytes(b"not a workbook 4111111111111111")
    item = FilesystemItem(path)

    assert list(XlsHandler().read(item, Config())) == []
    assert (item.unread_reason or "").startswith("Excel workbook could not be read: ")
    assert "4111111111111111" not in (item.unread_reason or ""), "parser text can quote the file"
    assert item.nothing_read


class _BookWithABadSheet(_FakeBook):
    def __init__(self) -> None:
        super().__init__(_FakeSheet([["KEPT"]]))

    def sheet_names(self) -> list[str]:
        return ["Bad", "Good"]

    def sheet_by_name(self, name: str) -> _FakeSheet:
        if name == "Bad":
            raise xls_mod.xlrd.XLRDError("damaged")
        return self._sheet


@pytest.mark.filehandlers
def test_xls_bad_sheet_keeps_the_other_sheets_and_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xls_mod.xlrd, "open_workbook", lambda *_a, **_k: _BookWithABadSheet())
    path = tmp_path / "fake.xls"
    path.write_bytes(b"x")
    item = FilesystemItem(path)

    assert list(XlsHandler().read(item, Config())) == ["KEPT"]
    assert item.unread_reason == "1 of 2 Excel sheets could not be read, first sheet 1: XLRDError"
    assert not item.nothing_read


@pytest.mark.filehandlers
def test_xls_password_protected_is_named_as_such(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def encrypted(*_a: object, **_k: object) -> None:
        raise xls_mod.xlrd.XLRDError("Workbook is encrypted")

    monkeypatch.setattr(xls_mod.xlrd, "open_workbook", encrypted)
    path = tmp_path / "locked.xls"
    path.write_bytes(b"x")
    item = FilesystemItem(path)

    assert list(XlsHandler().read(item, Config())) == []
    assert item.unread_reason == "Excel workbook is password protected"
    assert item.nothing_read
