import gc
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager

from pypdf import PdfReader
from pypdf.errors import (
    EmptyFileError,
    FileNotDecryptedError,
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
# bad page is skipped rather than costing the text of every page around it.  The
# same errors from the document as a whole keep whatever was read before them.
_PAGE_ERRORS = (PyPdfError, ValueError, KeyError, IndexError, TypeError, RecursionError)

# Fonts a document keeps parsed.  Real PDFs were measured using up to 18 on one
# page, and a page using more than this parses them all again each time.  An
# identity ToUnicode map was measured at about 11 MB, so a full cache costs about
# 350 MB.  A 131 MB document peaked at about 250 MB of its own, so the two together
# stay under the default 1 GB worker memory limit.
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


def _metadata_values(document: PdfReader) -> tuple[list[str], bool]:
    """The document's metadata as text, and whether any of it could not be read.

    Each entry is guarded on its own, so one bad /Title does not lose /Author.
    """
    values: list[str] = []
    unread = False
    try:
        metadata = document.metadata
        keys = list(metadata.keys()) if metadata else []
    except _PAGE_ERRORS:
        return values, True
    for key in keys:
        try:
            val = metadata.get(key)
            if val:
                values.append(str(val))
        except _PAGE_ERRORS:
            unread = True
    return values, unread


class PdfHandler:
    """FileHandler for PDF files.

    Reads via source.open_stream() — PdfReader accepts an IO[bytes] directly.
    pypdf's log level is set by orchestration.logging_setup, not here.
    Anything that could not be read goes to source.report_unread(): skipped
    pages, metadata, a locked or damaged document, a file that only claims to be
    a PDF.  So it keeps its findings without looking fully scanned.  Only an
    empty file is silent, because it holds nothing to miss.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        stream = source.open_stream()
        content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)
        pages_seen = 0
        pages_unread = 0
        text_read = False
        try:
            try:
                document = PdfReader(stream, strict=False)
                fonts = _FontCache()

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
                    text_read = True
                    for line in page_content.split("\n"):
                        content_buffer.append_content(line)
                        if content_buffer.content_buffer_full():
                            yield content_buffer.get_content()

                values, metadata_unread = _metadata_values(document)
                for value in values:
                    content_buffer.append_content(value)
                    if content_buffer.content_buffer_full():
                        yield content_buffer.get_content()
                if metadata_unread:
                    source.report_unread("PDF metadata could not be fully read")

            # All of these fall through: any text read before the failure is
            # still scanned.
            except EmptyFileError:
                pass
            except FileNotDecryptedError:
                source.report_unread("PDF is password protected", nothing_read=not text_read)
            except _PAGE_ERRORS as exc:
                stopped = f" after page {pages_seen:,}" if pages_seen else ""
                source.report_unread(
                    f"PDF could not be read{stopped}: {type(exc).__name__}: {exc}", nothing_read=not text_read
                )

            if pages_unread:
                source.report_unread(
                    f"{pages_unread:,} of {pages_seen:,} PDF pages could not be read", nothing_read=not text_read
                )

            final = content_buffer.finalize_content()
            if final:
                yield final
        finally:
            stream.close()


handler = PdfHandler()
