from collections.abc import Iterator

from pypdf import PdfReader
from pypdf.errors import (
    EmptyFileError,
    PdfReadError,
)

from piidigger.filehandlers._sharedfuncs import ContentBuffer
from piidigger.models.config import Config

# What pypdf can raise, even with strict=False, on a malformed content stream,
# font or metadata dictionary.  One bad page is skipped rather than costing the
# text of every page before it.
_PAGE_ERRORS = (ValueError, KeyError, IndexError, TypeError, RecursionError)

HANDLES = {
    "ext": [
        ".pdf",
    ],
    "mime": [
        "application/pdf",
    ],
}

handles = HANDLES


class PdfHandler:
    """FileHandler for PDF files.

    Reads via source.open_stream() — PdfReader accepts an IO[bytes] directly.
    pypdf's log level is set by orchestration.logging_setup, not here.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        stream = source.open_stream()
        try:
            document = PdfReader(stream, strict=False)
            content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)

            for page in document.pages:
                try:
                    page_content = page.extract_text()
                except _PAGE_ERRORS:
                    continue
                for line in page_content.split("\n"):
                    content_buffer.append_content(line)
                    if content_buffer.content_buffer_full():
                        yield content_buffer.get_content()

            try:
                metadata = document.metadata
            except _PAGE_ERRORS:
                metadata = None
            if metadata:
                for key in metadata.keys():
                    val = metadata.get(key)
                    if val:
                        content_buffer.append_content(str(val))
                        if content_buffer.content_buffer_full():
                            yield content_buffer.get_content()

            final = content_buffer.finalize_content()
            if final:
                yield final

        except (EmptyFileError, PdfReadError):
            return
        finally:
            stream.close()


handler = PdfHandler()
