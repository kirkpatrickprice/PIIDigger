import logging
from collections.abc import Iterator

from pypdf import PdfReader
from pypdf.errors import (
    EmptyFileError,
    PdfReadError,
)

from piidigger.filehandlers._sharedfuncs import ContentBuffer
from piidigger.models.config import Config

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
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        stream = source.open_stream()
        try:
            document = PdfReader(stream, strict=False)
            content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)

            for page in document.pages:
                page_content = page.extract_text()
                for line in page_content.split("\n"):
                    content_buffer.append_content(line)
                    if content_buffer.content_buffer_full():
                        yield content_buffer.get_content()

            metadata = document.metadata
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
