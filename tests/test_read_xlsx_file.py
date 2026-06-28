from pathlib import Path

import pytest

from piidigger.filehandlers.xlsx import XlsxHandler
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path) -> list[str]:
    return list(XlsxHandler().read(FilesystemItem(path)))


@pytest.mark.filehandlers
def test_xlsx_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/xlsx/does-not-exist.xlsx"))


@pytest.mark.filehandlers
def test_xlsx_empty_file() -> None:
    chunks = _read(Path("testdata/xlsx/empty-file.xlsx"))
    assert chunks == []


# Small, predictable fixtures: exact per-sheet chunk assertions.
# XlsxHandler yields one chunk per sheet; with DEFAULT_CHUNK_COUNT each sheet's
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
    # arrives as a single chunk with DEFAULT_CHUNK_COUNT.
    chunks = _read(Path("testdata/xlsx/random-data-table.xlsx"))
    content = " ".join(chunks)
    assert "First Name" in content
    assert "j.montgomery@randatmail.com" in content
    assert "Lower secondary" in content
