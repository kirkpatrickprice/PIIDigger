from pathlib import Path

import pytest

from piidigger.filehandlers.pdf import PdfHandler
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path) -> list[str]:
    return list(PdfHandler().read(FilesystemItem(path)))


@pytest.mark.filehandlers
def test_pdf_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/pdf/does-not-exist.pdf"))


@pytest.mark.filehandlers
def test_pdf_mislabeled() -> None:
    # Not a valid PDF — PdfReadError caught internally, yields nothing.
    chunks = _read(Path("testdata/pdf/mislabled-pdf-file.pdf"))
    assert chunks == []


@pytest.mark.filehandlers
def test_pdf_empty_body() -> None:
    # No page content, but PDF metadata is present.
    chunks = _read(Path("testdata/pdf/empty-file.pdf"))
    content = " ".join(chunks)
    assert "Randy Bartels" in content


@pytest.mark.filehandlers
def test_pdf_sample_pans() -> None:
    # Small file; single chunk containing the PAN values and metadata.
    chunks = _read(Path("testdata/pdf/sample-pans.pdf"))
    content = " ".join(chunks)
    assert "4893013335386137" in content
    assert "Randy Bartels" in content


@pytest.mark.filehandlers
def test_pdf_lorem_ipsum() -> None:
    # Multi-page document; with DEFAULT_CHUNK_COUNT the entire file arrives as
    # one or two large chunks.  Verify key content from the first and last pages.
    chunks = _read(Path("testdata/pdf/lorem-ipsum.pdf"))
    content = " ".join(chunks)
    assert "Lorem ipsum dolor sit amet" in content
    assert "Randy Bartels" in content
