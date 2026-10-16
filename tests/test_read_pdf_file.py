from pathlib import Path

import pytest

from piidigger.filehandlers.pdf import PdfHandler
from piidigger.models.config import Config
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path) -> list[str]:
    return list(PdfHandler().read(FilesystemItem(path), Config()))


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
    # Multi-page document; with the default buffer size the entire file arrives as
    # one or two large chunks.  Verify key content from the first and last pages.
    chunks = _read(Path("testdata/pdf/lorem-ipsum.pdf"))
    content = " ".join(chunks)
    assert "Lorem ipsum dolor sit amet" in content
    assert "Randy Bartels" in content


@pytest.mark.filehandlers
def test_pdf_bad_page_is_skipped_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # pypdf can raise a plain ValueError/KeyError on a malformed content stream,
    # even with strict=False.  One bad page must not cost the other pages.
    from pypdf import PageObject, PdfWriter

    two_pages = tmp_path / "two-pages.pdf"
    writer = PdfWriter()
    writer.append("testdata/pdf/sample-pans.pdf")
    writer.append("testdata/pdf/sample-pans.pdf")
    writer.add_metadata({"/Author": "Randy Bartels"})
    writer.write(two_pages)

    real_extract = PageObject.extract_text
    calls = {"n": 0}

    def flaky_extract(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyError("/Font")
        return real_extract(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", flaky_extract)

    content = " ".join(_read(two_pages))

    assert calls["n"] == 2, "the page after the bad one was never read"
    assert "4893013335386137" in content, "the second page's text was lost"
    assert "Randy Bartels" in content, "metadata after a bad page must still be read"


@pytest.mark.filehandlers
def test_pdf_bad_metadata_keeps_page_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfReader

    def broken_metadata(self):
        raise TypeError("malformed /Info dictionary")

    monkeypatch.setattr(PdfReader, "metadata", property(broken_metadata))

    content = " ".join(_read(Path("testdata/pdf/sample-pans.pdf")))

    assert "4893013335386137" in content
