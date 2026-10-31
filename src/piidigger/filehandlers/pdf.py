import gc
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager

from pypdf import PdfReader
from pypdf.errors import (
    EmptyFileError,
    FileNotDecryptedError,
    PdfReadError,
    PyPdfError,
)

from piidigger.filehandlers._sharedfuncs import ContentBuffer
from piidigger.models.config import Config

try:
    from pypdf.generic._font import Font as _Font
except ImportError:
    _Font = None

# What pypdf can raise, even with strict=False, on a malformed content stream,
# font or metadata dictionary, or on a page over one of its safety limits.  One
# bad page is skipped rather than costing the text of every page around it.
_PAGE_ERRORS = (PyPdfError, ValueError, KeyError, IndexError, TypeError, RecursionError)

# Fonts a document keeps parsed.  Real PDFs were measured using up to 18 on one
# page, and a page using more than this parses them all again each time.  An
# identity ToUnicode map costs 10-20 MB, so this stays well under the default
# 1 GB worker memory limit.
_MAX_CACHED_FONTS = 32

HANDLES = {
    "ext": [
        ".pdf",
    ],
    "mime": [
        "application/pdf",
    ],
}

handles = HANDLES


class _FontCache:
    """Parses each font once per document, for the fonts a document uses most recently.

    pypdf parses every font on a page again for every page.  A font whose
    ToUnicode map is the identity range <0000> <FFFF> <0000>, as mPDF writes,
    expands to 65,536 entries: about a second per page, and reference cycles
    that outgrow the worker memory limit within a few dozen pages.

    Font.from_font_resource is private, and has moved between pypdf releases.
    tests/test_read_pdf_file.py fails if a version bump removes it.

    A dropped font is a reference cycle like any other, so dropping one triggers
    a collection.  So does every page when pypdf no longer has that method.

    A cached entry keeps its font dictionary alive, so its id() cannot be reused
    by another dictionary while the entry exists.
    """

    def __init__(self) -> None:
        self._fonts: OrderedDict[int, tuple[object, object]] = OrderedDict()
        self._font_dropped = False

    @contextmanager
    def active(self) -> Iterator[None]:
        """Use the cache for the duration of one extract_text() call.

        Never held across a yield of the file handler, so documents cannot
        overlap here and each restore puts back the real method.
        """
        if _Font is None or "from_font_resource" not in vars(_Font):
            try:
                yield
            finally:
                gc.collect()
            return

        original = vars(_Font)["from_font_resource"]

        def cached(cls, pdf_font_dict):
            key = id(pdf_font_dict)
            entry = self._fonts.get(key)
            if entry is not None:
                self._fonts.move_to_end(key)
                return entry[1]
            font = original.__get__(None, cls)(pdf_font_dict)
            self._fonts[key] = (pdf_font_dict, font)
            if len(self._fonts) > _MAX_CACHED_FONTS:
                self._fonts.popitem(last=False)
                self._font_dropped = True
            return font

        _Font.from_font_resource = classmethod(cached)
        try:
            yield
        finally:
            _Font.from_font_resource = original
            if self._font_dropped:
                self._font_dropped = False
                gc.collect()


class PdfHandler:
    """FileHandler for PDF files.

    Reads via source.open_stream() — PdfReader accepts an IO[bytes] directly.
    pypdf's log level is set by orchestration.logging_setup, not here.
    Pages that could not be read go to source.report_unread(), so a partly read
    document keeps its findings without looking fully scanned.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        stream = source.open_stream()
        content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)
        try:
            try:
                document = PdfReader(stream, strict=False)
                fonts = _FontCache()
                pages_seen = 0
                pages_unread = 0

                for page in document.pages:
                    pages_seen += 1
                    try:
                        with fonts.active():
                            page_content = page.extract_text()
                    except FileNotDecryptedError:
                        raise
                    except _PAGE_ERRORS:
                        pages_unread += 1
                        continue
                    finally:
                        # A finished page is real progress.  Text is still chunked
                        # by the content buffer, so a match is never split at a
                        # page break; pypdf stuck inside one page reports nothing.
                        source.report_progress()
                    for line in page_content.split("\n"):
                        content_buffer.append_content(line)
                        if content_buffer.content_buffer_full():
                            yield content_buffer.get_content()

                if pages_unread:
                    source.report_unread(f"{pages_unread:,} of {pages_seen:,} PDF pages could not be read")

                try:
                    metadata = document.metadata
                    if metadata:
                        for key in metadata.keys():
                            val = metadata.get(key)
                            if val:
                                content_buffer.append_content(str(val))
                                if content_buffer.content_buffer_full():
                                    yield content_buffer.get_content()
                except _PAGE_ERRORS:
                    pass

            except EmptyFileError, PdfReadError:
                # Falls through: any text read before the failure is still scanned.
                pass

            final = content_buffer.finalize_content()
            if final:
                yield final
        finally:
            stream.close()


handler = PdfHandler()
