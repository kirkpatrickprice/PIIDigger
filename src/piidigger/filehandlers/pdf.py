import logging
from collections.abc import Iterator

from pypdf import PdfReader
from pypdf.errors import (
    EmptyFileError,
    PdfReadError,
)

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import DEFAULT_CHUNK_COUNT, MAX_CHUNK_SIZE

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

    def read(self, source) -> Iterator[str]:  # source: ScannableItem
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        stream = source.open_stream()
        try:
            document = PdfReader(stream, strict=False)
            chunk_handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * DEFAULT_CHUNK_COUNT)

            for page in document.pages:
                page_content = page.extract_text()
                for line in page_content.split("\n"):
                    chunk_handler.append_content(line)
                    if chunk_handler.content_buffer_full():
                        yield chunk_handler.get_content()

            metadata = document.metadata
            if metadata:
                for key in metadata.keys():
                    val = metadata.get(key)
                    if val:
                        chunk_handler.append_content(str(val))
                        if chunk_handler.content_buffer_full():
                            yield chunk_handler.get_content()

            final = chunk_handler.finalize_content()
            if final:
                yield final

        except EmptyFileError, PdfReadError:
            return
        finally:
            stream.close()


handler = PdfHandler()
