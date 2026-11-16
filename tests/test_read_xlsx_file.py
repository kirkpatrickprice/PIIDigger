import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from piidigger.filehandlers.xlsx import XlsxHandler
from piidigger.models.config import Config, SpreadsheetConfig
from piidigger.orchestration.sources import FilesystemItem
from tests._ole import encrypted_office_file, ole_file


def _read(path: Path, config: Config | None = None) -> list[str]:
    return list(XlsxHandler().read(FilesystemItem(path), config or Config()))


@pytest.mark.filehandlers
def test_xlsx_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/xlsx/does-not-exist.xlsx"))


@pytest.mark.filehandlers
def test_xlsx_empty_file() -> None:
    chunks = _read(Path("testdata/xlsx/empty-file.xlsx"))
    assert chunks == []


# Small, predictable fixtures: exact per-sheet chunk assertions.
# XlsxHandler yields one chunk per sheet; with the default buffer size each sheet's
# entire content fits in a single chunk.
@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "filename, expected_chunks",
    [
        (
            "testdata/xlsx/test-1sheet-1cell.xlsx",
            ["S1R1C1"],
        ),
        (
            "testdata/xlsx/test-1sheet-1cell-carriage-return.xlsx",
            ["S1R1C1L1 S1R1C1L2"],
        ),
        (
            "testdata/xlsx/test-1sheet-1row.xlsx",
            ["S1R1C1 S1R1C2 S1R1C3 S1R1C4"],
        ),
        (
            "testdata/xlsx/test-1sheet-10row-table.xlsx",
            [
                "Sheet Row Column Text 1 1 3 S1R1C3 1 2 3 S1R2C3 1 3 3 S1R3C3 1 4 3 S1R4C3 1 5 3 S1R5C3 1 6 3 S1R6C3 1 7 3 S1R7C3 1 8 3 S1R8C3 1 9 3 S1R9C3 1 10 3 S1R10C3"
            ],
        ),
        (
            "testdata/xlsx/test-2sheet-10row-table.xlsx",
            [
                "Sheet Row Column Text 1 1 3 S1R1C3 1 2 3 S1R2C3 1 3 3 S1R3C3 1 4 3 S1R4C3 1 5 3 S1R5C3 1 6 3 S1R6C3 1 7 3 S1R7C3 1 8 3 S1R8C3 1 9 3 S1R9C3 1 10 3 S1R10C3",
                "Sheet Row Column Text 1 1 3 S1R1C3 1 2 3 S1R2C3 1 3 3 S1R3C3 1 4 3 S1R4C3 1 5 3 S1R5C3 1 6 3 S1R6C3 1 7 3 S1R7C3 1 8 3 S1R8C3 1 9 3 S1R9C3 1 10 3 S1R10C3",
            ],
        ),
    ],
)
def test_xlsx_exact_content(filename: str, expected_chunks: list[str]) -> None:
    chunks = _read(Path(filename))
    assert chunks == expected_chunks


@pytest.mark.filehandlers
def test_xlsx_random_data_table() -> None:
    # Large table that was split into 22 chunks with maxChunkCount=2; now
    # arrives as a single chunk with the default buffer size.
    chunks = _read(Path("testdata/xlsx/random-data-table.xlsx"))
    content = " ".join(chunks)
    assert "First Name" in content
    assert "j.montgomery@randatmail.com" in content
    assert "Lower secondary" in content


@pytest.mark.filehandlers
def test_xlsx_blank_row_limit_stops_early(tmp_path: Path) -> None:
    # One value, a run of blank rows, then a second value further down.
    workbook_path = tmp_path / "blank-run.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "FIRST"
    ws["A10"] = "SECOND"  # 8 blank rows (2-9) separate the two values
    wb.save(workbook_path)

    default_content = " ".join(_read(workbook_path))
    assert "FIRST" in default_content
    assert "SECOND" in default_content

    strict_config = Config(spreadsheet=SpreadsheetConfig(blank_row_limit=3))
    strict_content = " ".join(_read(workbook_path, strict_config))
    assert "FIRST" in strict_content
    assert "SECOND" not in strict_content


@pytest.mark.filehandlers
def test_xlsx_blank_col_limit_counts_consecutive_blanks_only(tmp_path: Path) -> None:
    """Scattered blanks must not add up to the limit.

    Regression test: the counter was never reset by a non-blank cell, so a row
    with alternating data and blank columns stopped after blank_col_limit blanks
    in total, dropping every value after that point.
    """
    workbook_path = tmp_path / "scattered-blanks.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    for col, value in ((1, "A"), (4, "B"), (7, "C"), (10, "4111111111111111")):
        ws.cell(row=1, column=col, value=value)  # two blank columns between each value
    wb.save(workbook_path)

    content = " ".join(_read(workbook_path, Config(spreadsheet=SpreadsheetConfig(blank_col_limit=2))))

    assert content.split() == ["A", "B", "C", "4111111111111111"]


@pytest.mark.filehandlers
def test_xlsx_blank_col_limit_still_stops_on_a_consecutive_run(tmp_path: Path) -> None:
    workbook_path = tmp_path / "blank-run.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=1, column=1, value="FIRST")
    ws.cell(row=1, column=6, value="SECOND")  # four consecutive blank columns before it
    wb.save(workbook_path)

    content = " ".join(_read(workbook_path, Config(spreadsheet=SpreadsheetConfig(blank_col_limit=3))))

    assert "FIRST" in content
    assert "SECOND" not in content


def _workbook_with_chart_sheet(path: Path) -> None:
    """Two worksheets with a chart sheet between them."""
    from openpyxl.chart import BarChart, Reference

    wb = openpyxl.Workbook()
    first = wb.active
    first.title = "First"
    first.append(["FIRSTSHEET", 1])
    chart = BarChart()
    chart.add_data(Reference(first, min_col=2, min_row=1, max_row=1))
    wb.create_chartsheet("Chart").add_chart(chart)
    wb.create_sheet("Last").append(["LASTSHEET"])
    wb.save(path)


class _InMemorySource:
    """A source that offers its bytes, as an archive member does."""

    def __init__(self, path: Path) -> None:
        self._data = path.read_bytes()

    def open_bytes(self) -> bytes:
        return self._data


@pytest.mark.filehandlers
@pytest.mark.parametrize("in_memory", [False, True], ids=["on-disk", "in-memory"])
def test_xlsx_chart_sheet_is_skipped_not_fatal(tmp_path: Path, in_memory: bool) -> None:
    # A chart sheet has no cells.  Reading it used to raise after the earlier
    # sheets were yielded, and the scan dropped the whole workbook's findings.
    path = tmp_path / "with-chart.xlsx"
    _workbook_with_chart_sheet(path)
    source = _InMemorySource(path) if in_memory else FilesystemItem(path)

    content = " ".join(XlsxHandler().read(source, Config()))

    assert "FIRSTSHEET" in content
    assert "LASTSHEET" in content


@pytest.mark.filehandlers
def test_xlsx_handler_selected_for_excel_templates() -> None:
    from piidigger.filehandlers import get_handler_for

    assert isinstance(get_handler_for(".xltx", None), XlsxHandler)


@pytest.mark.filehandlers
def test_xlsx_without_an_excel_extension_is_read(tmp_path: Path) -> None:
    """MIME detection picks the handler for extensionless files; openpyxl must not reject the name."""
    book = openpyxl.Workbook()
    book.active.append(["card", "4111111111111111"])
    path = tmp_path / "upload-0001"
    book.save(path)

    chunks = _read(path)

    assert any("4111111111111111" in chunk for chunk in chunks)


@pytest.mark.filehandlers
def test_xlsx_zero_bytes_is_not_reported(tmp_path: Path) -> None:
    path = tmp_path / "zero.xlsx"
    path.write_bytes(b"")
    item = FilesystemItem(path)

    assert list(XlsxHandler().read(item, Config())) == []
    assert item.unread_reason is None
    assert not item.nothing_read


@pytest.mark.filehandlers
def test_xlsx_not_a_workbook_is_reported_in_the_shared_wording(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not a workbook 4111111111111111")
    item = FilesystemItem(path)

    assert list(XlsxHandler().read(item, Config())) == []
    assert (item.unread_reason or "").startswith("Excel workbook could not be read: ")
    assert "4111111111111111" not in (item.unread_reason or ""), "parser text can quote the file"
    assert item.nothing_read


def _rewrite_part(path: Path, part: str, rewrite: Callable[[bytes], bytes]) -> None:
    copy = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(copy, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            target.writestr(info, rewrite(data) if info.filename == part else data)
    copy.replace(path)


@pytest.mark.filehandlers
def test_xlsx_sheet_damaged_part_way_keeps_its_rows_and_the_other_sheets(tmp_path: Path) -> None:
    path = tmp_path / "bad-sheet.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["FIRSTSHEET"])
    wb.create_sheet("Second").append(["SECONDSHEET"])
    wb.create_sheet("Third").append(["THIRDSHEET"])
    wb.save(path)
    _rewrite_part(path, "xl/worksheets/sheet2.xml", lambda data: data.replace(b"</sheetData>", b"<broken"))
    item = FilesystemItem(path)

    content = " ".join(XlsxHandler().read(item, Config()))

    assert "FIRSTSHEET" in content
    assert "SECONDSHEET" in content, "rows read before the damage were still only buffered"
    assert "THIRDSHEET" in content
    assert (item.unread_reason or "").startswith("1 of 3 Excel sheets could not be read, first sheet 2: ")
    assert not item.nothing_read


@pytest.mark.filehandlers
def test_xlsx_malformed_sheet_that_stops_the_load_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "bad-sheet.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["FIRSTSHEET"])
    wb.save(path)
    _rewrite_part(path, "xl/worksheets/sheet1.xml", lambda _data: b"<worksheet><broken")
    item = FilesystemItem(path)

    assert list(XlsxHandler().read(item, Config())) == []
    assert item.unread_reason == "Excel workbook could not be read: ParseError"
    assert item.nothing_read


@pytest.mark.filehandlers
def test_xlsx_access_denied_still_reaches_the_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "locked.xlsx"
    openpyxl.Workbook().save(path)
    item = FilesystemItem(path)

    def denied() -> None:
        raise PermissionError("access denied")

    monkeypatch.setattr(item, "open_stream", denied)

    with pytest.raises(PermissionError):
        list(XlsxHandler().read(item, Config()))


@pytest.mark.filehandlers
def test_xlsx_password_protected_is_named_as_such(tmp_path: Path) -> None:
    """Office saves an encrypted .xlsx as an OLE compound file, which used to read as a damaged one."""
    path = tmp_path / "locked.xlsx"
    path.write_bytes(encrypted_office_file())
    item = FilesystemItem(path)

    assert list(XlsxHandler().read(item, Config())) == []
    assert item.unread_reason == "Excel workbook is password protected"
    assert item.nothing_read
    assert item.password_protected


@pytest.mark.filehandlers
def test_xlsx_other_compound_file_is_not_called_password_protected(tmp_path: Path) -> None:
    """A legacy Office file under a .xlsx name is an OLE compound file too, with no EncryptedPackage."""
    path = tmp_path / "legacy.xlsx"
    path.write_bytes(ole_file({"WordDocument": b"\x00" * 16, "1Table": b"\x00" * 16}))
    item = FilesystemItem(path)

    assert list(XlsxHandler().read(item, Config())) == []
    assert item.unread_reason == "Excel workbook could not be read: BadZipFile"
    assert not item.password_protected


@pytest.mark.filehandlers
def test_xlsx_damaged_compound_file_is_not_called_password_protected(tmp_path: Path) -> None:
    path = tmp_path / "damaged.xlsx"
    path.write_bytes(encrypted_office_file()[:600])  # the header and FAT, but no directory
    item = FilesystemItem(path)

    assert list(XlsxHandler().read(item, Config())) == []
    assert item.unread_reason == "Excel workbook could not be read: BadZipFile"
    assert not item.password_protected


@pytest.mark.filehandlers
def test_xlsx_encrypted_package_stored_in_pieces_is_found_without_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check reads the directory listing; locate_named_stream() would copy the whole stream."""
    import xlrd.compdoc

    def no_copy(*_a: object, **_k: object) -> None:
        raise AssertionError("the stream itself must not be located")

    monkeypatch.setattr(xlrd.compdoc.CompDoc, "locate_named_stream", no_copy)
    path = tmp_path / "locked.xlsx"
    path.write_bytes(encrypted_office_file())
    item = FilesystemItem(path)

    assert list(XlsxHandler().read(item, Config())) == []
    assert item.password_protected


class _CountingItem(FilesystemItem):
    """A FilesystemItem whose stream counts the bytes read, and can fail after the signature."""

    def __init__(self, path: Path, *, fail: bool = False) -> None:
        super().__init__(path)
        self.bytes_read = 0
        self._fail = fail

    def open_stream(self) -> Any:
        item = self
        stream = super().open_stream()

        class Counting:
            def read(self, size: int = -1) -> bytes:
                if item._fail and item.bytes_read:
                    raise OSError(5, "Input/output error")
                data = stream.read(size)
                item.bytes_read += len(data)
                return data

            def seek(self, offset: int, whence: int = 0) -> int:
                return stream.seek(offset, whence)

            def __enter__(self) -> Counting:
                return self

            def __exit__(self, *_exc: object) -> None:
                stream.close()

        return Counting()


@pytest.mark.filehandlers
def test_xlsx_encryption_check_reads_only_the_directory_not_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An encrypted workbook can be hundreds of MB; only its header, allocation table and directory are needed.

    Counted at the disk: a buffered read would fetch 128 KiB for each sector.
    """
    import piidigger._progress_io as progress_io
    import piidigger.filehandlers._sharedfuncs as shared

    path = tmp_path / "locked.xlsx"
    path.write_bytes(encrypted_office_file() + bytes(4 * 1024 * 1024))  # the encrypted data, as unused sectors
    from_disk: list[int] = []
    real_readinto = progress_io._ProgressRaw.readinto

    def counting_readinto(self: Any, buffer: Any) -> int:
        count = real_readinto(self, buffer)
        from_disk.append(count)
        return count

    monkeypatch.setattr(progress_io._ProgressRaw, "readinto", counting_readinto)

    assert shared.is_encrypted_office_file(FilesystemItem(path))
    assert 0 < sum(from_disk) < 8 * 1024, from_disk


@pytest.mark.filehandlers
def test_xlsx_read_error_during_the_encryption_check_reaches_the_caller(tmp_path: Path) -> None:
    """A dropped share is an OSError for the worker to report, not a guess that the file isn't encrypted."""
    import piidigger.filehandlers._sharedfuncs as shared

    path = tmp_path / "locked.xlsx"
    path.write_bytes(encrypted_office_file())

    with pytest.raises(OSError, match="Input/output error"):
        shared.is_encrypted_office_file(_CountingItem(path, fail=True))
