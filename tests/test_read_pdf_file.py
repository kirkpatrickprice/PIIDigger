import gc
from pathlib import Path

import psutil
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
@pytest.mark.parametrize("body", [b"plain text, not a PDF", b"%PDF-1.4\ngarbage after the header"])
def test_pdf_mislabeled(tmp_path: Path, body: bytes) -> None:
    # Not a valid PDF: nothing to yield, and it was not scanned, so it must not look clean.
    path = tmp_path / "mislabeled.pdf"
    path.write_bytes(body)
    item = FilesystemItem(path)

    assert list(PdfHandler().read(item, Config())) == []
    assert (item.unread_reason or "").startswith("PDF could not be read: ")
    assert item.nothing_read


@pytest.mark.filehandlers
def test_pdf_zero_bytes_is_not_reported() -> None:
    item = FilesystemItem(Path("testdata/pdf/mislabled-pdf-file.pdf"))

    assert list(PdfHandler().read(item, Config())) == []
    assert item.unread_reason is None


def _encrypted_pdf(tmp_path: Path, algorithm: str) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret", algorithm=algorithm)
    path = tmp_path / "locked.pdf"
    with path.open("wb") as f:
        writer.write(f)
    return path


@pytest.mark.filehandlers
@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-256"])
def test_pdf_password_protected_is_reported(tmp_path: Path, algorithm: str) -> None:
    item = FilesystemItem(_encrypted_pdf(tmp_path, algorithm))

    assert list(PdfHandler().read(item, Config())) == []
    assert item.unread_reason == "PDF is password protected"
    assert item.nothing_read


@pytest.mark.filehandlers
def test_pdf_truncated_is_reported(tmp_path: Path) -> None:
    raw = Path("testdata/pan/sample-pans.pdf").read_bytes()
    path = tmp_path / "cut.pdf"
    path.write_bytes(raw[: len(raw) // 2])
    item = FilesystemItem(path)

    list(PdfHandler().read(item, Config()))

    assert item.unread_reason is not None


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
    item = FilesystemItem(Path("testdata/pdf/sample-pans.pdf"))

    content = " ".join(PdfHandler().read(item, Config()))

    assert "4893013335386137" in content
    assert item.unread_reason == "PDF metadata could not be fully read"


@pytest.mark.filehandlers
def test_pdf_reports_progress_once_per_page(tmp_path: Path) -> None:
    """A finished page keeps a long scan alive, without changing how text is chunked."""
    from pypdf import PdfReader, PdfWriter

    page = PdfReader("testdata/pdf/lorem-ipsum.pdf").pages[0]
    writer = PdfWriter()
    for _ in range(3):
        writer.add_page(page)
    path = tmp_path / "three-pages.pdf"
    writer.write(path)
    pages = 3

    class _CountingItem(FilesystemItem):
        def __init__(self, p: Path) -> None:
            super().__init__(p)
            self.pages_done = 0

        def report_progress(self) -> None:
            self.pages_done += 1

    item = _CountingItem(path)
    chunks = list(PdfHandler().read(item, Config()))

    assert pages > 1
    assert item.pages_done == pages
    assert len(chunks) == 1, "pages still share one content buffer"


def _identity_cmap_pdf(
    path: Path,
    pages: int,
    text: str,
    *,
    font_per_page: bool = False,
    bad_pages: dict[int, bytes] | None = None,
) -> None:
    """Write a PDF using the 65,536-entry ToUnicode map mPDF writes.

    All pages share one font unless font_per_page, as in merged invoices.
    bad_pages replaces the content stream of the pages it names (0-based).
    """
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    cmap = DecodedStreamObject()
    cmap.set_data(
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"1 beginbfrange\n<0000> <FFFF> <0000>\nendbfrange\nendcmap\n"
    )
    writer = PdfWriter()

    def new_font():
        descendant = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/CIDFontType2"),
                NameObject("/BaseFont"): NameObject("/Probe"),
                NameObject("/DW"): NumberObject(600),
            }
        )
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type0"),
                NameObject("/BaseFont"): NameObject("/Probe"),
                NameObject("/Encoding"): NameObject("/Identity-H"),
                NameObject("/DescendantFonts"): ArrayObject([writer._add_object(descendant)]),
                NameObject("/ToUnicode"): writer._add_object(cmap),
            }
        )
        return writer._add_object(font)

    shared_font = new_font()
    good_stream = f"BT /F1 12 Tf 72 720 Td <{text.encode('utf-16-be').hex()}> Tj ET".encode()
    for index in range(pages):
        content = DecodedStreamObject()
        content.set_data((bad_pages or {}).get(index, good_stream))
        page = writer.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): new_font() if font_per_page else shared_font})}
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(path)


class _RecordingItem(FilesystemItem):
    """Samples this process's memory at each finished page, while the read is under way."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.peak_rss = psutil.Process().memory_info().rss

    def report_progress(self) -> None:
        self.peak_rss = max(self.peak_rss, psutil.Process().memory_info().rss)


@pytest.mark.filehandlers
def test_pdf_font_cache_targets_a_real_pypdf_method() -> None:
    """Fails on a pypdf bump that moves the private method the cache wraps.

    The handler would still work, through the slower gc.collect() fallback, so
    nothing else would notice.
    """
    from piidigger.filehandlers import pdf

    assert pdf._Font is not None, "pypdf.generic._font.Font has moved; update the import in pdf.py"
    assert "from_font_resource" in vars(pdf._Font)


def _count_font_parses(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    from piidigger.filehandlers import pdf

    original = vars(pdf._Font)["from_font_resource"]
    calls = {"n": 0}

    def counting(cls, pdf_font_dict):
        calls["n"] += 1
        return original.__get__(None, cls)(pdf_font_dict)

    monkeypatch.setattr(pdf._Font, "from_font_resource", classmethod(counting))
    return calls


@pytest.mark.filehandlers
def test_pdf_font_is_parsed_once_per_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "identity-cmap.pdf"
    _identity_cmap_pdf(path, pages=4, text="4893013335386137")
    calls = _count_font_parses(monkeypatch)

    content = " ".join(_read(path))

    assert calls["n"] == 1, "a font was parsed again for a later page"
    assert content.count("4893013335386137") == 4


@pytest.mark.filehandlers
def test_pdf_font_cache_is_only_installed_during_extraction(tmp_path: Path) -> None:
    """The patch is off whenever the generator is suspended, so documents cannot overlap."""
    from piidigger.filehandlers import pdf

    path = tmp_path / "identity-cmap.pdf"
    _identity_cmap_pdf(path, pages=1, text="4893013335386137")
    original = vars(pdf._Font)["from_font_resource"]

    reader = PdfHandler().read(FilesystemItem(path), Config())
    assert "4893013335386137" in next(reader)
    assert vars(pdf._Font)["from_font_resource"] is original
    reader.close()
    assert vars(pdf._Font)["from_font_resource"] is original


@pytest.mark.filehandlers
def test_pdf_font_cache_is_not_shared_between_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _identity_cmap_pdf(first, pages=1, text="4893013335386137")
    _identity_cmap_pdf(second, pages=1, text="5105105105105100")
    calls = _count_font_parses(monkeypatch)

    assert "4893013335386137" in " ".join(_read(first))
    assert "5105105105105100" in " ".join(_read(second))
    assert calls["n"] == 2


@pytest.mark.filehandlers
def test_pdf_shared_font_memory_stays_bounded(tmp_path: Path) -> None:
    """Without the cache, each page leaves its 65,536-entry font map behind."""
    path = tmp_path / "identity-cmap.pdf"
    _identity_cmap_pdf(path, pages=40, text="4893013335386137")
    gc.collect()
    before = psutil.Process().memory_info().rss

    item = _RecordingItem(path)
    chunks = list(PdfHandler().read(item, Config()))

    assert " ".join(chunks).count("4893013335386137") == 40
    assert item.peak_rss - before < 150 * 1024 * 1024


@pytest.mark.filehandlers
def test_pdf_font_cache_drops_the_oldest_font_and_collects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PDF with a new font on every page must not keep every font it has seen."""
    from piidigger.filehandlers import pdf

    path = tmp_path / "merged.pdf"
    _identity_cmap_pdf(path, pages=5, text="4893013335386137", font_per_page=True)
    monkeypatch.setattr(pdf, "_MAX_CACHED_FONTS", 2)
    caches: list[pdf._FontCache] = []

    class _SpyCache(pdf._FontCache):
        def __init__(self) -> None:
            super().__init__()
            caches.append(self)

    monkeypatch.setattr(pdf, "_FontCache", _SpyCache)
    collected = {"n": 0}
    monkeypatch.setattr(pdf.gc, "collect", lambda: collected.__setitem__("n", collected["n"] + 1))

    content = " ".join(_read(path))

    assert content.count("4893013335386137") == 5
    assert len(caches[0]._fonts) == 2
    assert collected["n"] == 3, "pages 3, 4 and 5 each drop a font"


@pytest.mark.filehandlers
def test_pdf_font_reused_after_others_stays_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only fonts over the cap are dropped: a shared font is not parsed again at the cap."""
    from piidigger.filehandlers import pdf

    path = tmp_path / "identity-cmap.pdf"
    _identity_cmap_pdf(path, pages=3, text="4893013335386137")
    monkeypatch.setattr(pdf, "_MAX_CACHED_FONTS", 1)
    calls = _count_font_parses(monkeypatch)

    assert " ".join(_read(path)).count("4893013335386137") == 3
    assert calls["n"] == 1


@pytest.mark.filehandlers
def test_pdf_without_the_font_cache_collects_garbage_per_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If pypdf drops the private method, memory stays bounded the slower way."""
    from piidigger.filehandlers import pdf

    path = tmp_path / "identity-cmap.pdf"
    _identity_cmap_pdf(path, pages=2, text="4893013335386137")
    monkeypatch.setattr(pdf, "_Font", None)
    collected = {"n": 0}
    monkeypatch.setattr(pdf.gc, "collect", lambda: collected.__setitem__("n", collected["n"] + 1))

    content = " ".join(_read(path))

    assert collected["n"] == 2
    assert content.count("4893013335386137") == 2


@pytest.mark.filehandlers
def test_pdf_page_over_a_pypdf_limit_is_skipped_and_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PageObject
    from pypdf.errors import LimitReachedError

    path = tmp_path / "two-pages.pdf"
    _identity_cmap_pdf(path, pages=2, text="4893013335386137")
    real_extract = PageObject.extract_text
    calls = {"n": 0}

    def limited_extract(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LimitReachedError("over a limit")
        return real_extract(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", limited_extract)
    item = FilesystemItem(path)

    content = " ".join(PdfHandler().read(item, Config()))

    assert content.count("4893013335386137") == 1
    assert item.unread_reason == "1 of 2 PDF pages could not be read"


@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "bad_stream",
    [b"BT <41", b"BT ET BI /W 1 /H 1 /CS /G /BPC 8 ID abc", b"BT /F1 12 Tf ) Tj ET"],
    ids=["unterminated-hex", "inline-image-no-end", "stray-paren"],
)
def test_pdf_malformed_page_keeps_the_pages_around_it(tmp_path: Path, bad_stream: bytes) -> None:
    """pypdf raises PdfStreamError here, which is a PdfReadError but only fails one page."""
    path = tmp_path / "three-pages.pdf"
    _identity_cmap_pdf(path, pages=3, text="4893013335386137", bad_pages={1: bad_stream})
    item = FilesystemItem(path)

    content = " ".join(PdfHandler().read(item, Config()))

    assert content.count("4893013335386137") == 2
    assert item.unread_reason == "1 of 3 PDF pages could not be read"


@pytest.mark.filehandlers
def test_pdf_bad_metadata_entry_keeps_buffered_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    class _BadMetadata(dict):
        def get(self, key, *args):
            if key == "/Title":
                raise PdfReadError("bad /Info entry")
            return super().get(key, *args)

    monkeypatch.setattr(
        PdfReader, "metadata", property(lambda self: _BadMetadata({"/Title": "x", "/Author": "someone@example.com"}))
    )
    item = FilesystemItem(Path("testdata/pdf/sample-pans.pdf"))

    content = " ".join(PdfHandler().read(item, Config()))

    assert "4893013335386137" in content
    assert "someone@example.com" in content, "one bad entry must not lose the entries after it"
    assert item.unread_reason == "PDF metadata could not be fully read"


@pytest.mark.filehandlers
def test_pdf_failure_after_some_pages_still_yields_their_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PdfReadError from outside any one page must not lose what was buffered."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    real_pages = PdfReader.pages.fget

    def pages_then_failure(self):
        yield real_pages(self)[0]
        raise PdfReadError("broken page tree")

    monkeypatch.setattr(PdfReader, "pages", property(pages_then_failure))
    item = FilesystemItem(Path("testdata/pdf/sample-pans.pdf"))

    assert "4893013335386137" in " ".join(PdfHandler().read(item, Config()))
    assert item.unread_reason == "PDF could not be read after page 1: PdfReadError: broken page tree"
    assert not item.nothing_read, "the first page was read"


@pytest.mark.filehandlers
def test_pdf_malformed_page_tree_keeps_buffered_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """pypdf can raise more than PdfReadError from the page tree, and that must not lose the text read so far."""
    from pypdf import PdfReader

    real_pages = PdfReader.pages.fget

    def pages_then_failure(self):
        yield real_pages(self)[0]
        raise KeyError("/Kids")

    monkeypatch.setattr(PdfReader, "pages", property(pages_then_failure))
    item = FilesystemItem(Path("testdata/pdf/sample-pans.pdf"))

    assert "4893013335386137" in " ".join(PdfHandler().read(item, Config()))
    assert "KeyError" in (item.unread_reason or "")


@pytest.mark.filehandlers
def test_pdf_reports_skipped_pages_along_with_a_later_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PageObject, PdfReader
    from pypdf.errors import PdfReadError

    real_pages = PdfReader.pages.fget

    def pages_then_failure(self):
        yield real_pages(self)[0]
        raise PdfReadError("broken page tree")

    def bad_page(self, *args, **kwargs):
        raise ValueError("bad page")

    monkeypatch.setattr(PdfReader, "pages", property(pages_then_failure))
    monkeypatch.setattr(PageObject, "extract_text", bad_page)
    item = FilesystemItem(Path("testdata/pdf/sample-pans.pdf"))

    list(PdfHandler().read(item, Config()))

    assert item.unread_reason == (
        "PDF could not be read after page 1: PdfReadError: broken page tree; 1 of 1 PDF pages could not be read"
    )


@pytest.mark.filehandlers
def test_pdf_not_decrypted_is_not_treated_as_a_page_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PageObject
    from pypdf.errors import FileNotDecryptedError

    def locked(self, *args, **kwargs):
        raise FileNotDecryptedError("file has not been decrypted")

    monkeypatch.setattr(PageObject, "extract_text", locked)
    item = FilesystemItem(Path("testdata/pdf/sample-pans.pdf"))

    list(PdfHandler().read(item, Config()))

    assert item.unread_reason == "PDF is password protected", "a locked document is not one bad page"


@pytest.mark.filehandlers
def test_pdf_with_every_page_unreadable_read_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PageObject

    def bad_page(self, *args, **kwargs):
        raise ValueError("bad page")

    monkeypatch.setattr(PageObject, "extract_text", bad_page)
    item = FilesystemItem(Path("testdata/pdf/sample-pans.pdf"))

    list(PdfHandler().read(item, Config()))

    assert item.nothing_read
