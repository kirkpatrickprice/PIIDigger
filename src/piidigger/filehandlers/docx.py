"""Process DOCX files"""

import warnings
from collections.abc import Iterator
from io import BytesIO
from zipfile import BadZipFile

from docx2python import docx2python
from docx2python.iterators import iter_paragraphs

from piidigger.filehandlers._constants import DEFAULT_CHUNK_COUNT, MAX_CHUNK_SIZE
from piidigger.filehandlers._sharedfuncs import ContentHandler

warnings.filterwarnings("ignore", category=UserWarning, module="docx2python")

HANDLES = {
    "ext": [
        ".docx",
    ],
    "mime": [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
}

handles = HANDLES


class DocxHandler:
    """FileHandler for DOCX files.

    Preferred path (archive members): source.open_bytes() returns bytes which
    are wrapped in BytesIO and passed directly to docx2python — no temp file.

    Fallback path (on-disk files): source.open_bytes() returns None, so
    source.materialize() is called to obtain a filesystem path.  For
    FilesystemItem materialize() is a no-op (returns the path itself).
    """

    def read(self, source) -> Iterator[str]:  # source: ScannableItem
        data = source.open_bytes()
        docx_arg: BytesIO | str = BytesIO(data) if data is not None else str(source.materialize())
        chunk_handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * DEFAULT_CHUNK_COUNT)

        try:
            docx_content = docx2python(docx_arg)
            # .document is a lazy property that opens the ZIP — catch corruption here
            document_lines = list(iter_paragraphs(docx_content.document))
        except BadZipFile:
            return

        for line in document_lines:
            chunk_handler.append_content(line)
            if chunk_handler.content_buffer_full():
                yield chunk_handler.get_content()

        for comment in docx_content.comments:
            if comment is not None:
                chunk_handler.append_content(comment[3])
                if chunk_handler.content_buffer_full():
                    yield chunk_handler.get_content()

        chunk_handler.append_content(str(docx_content.core_properties))

        final = chunk_handler.finalize_content()
        if final:
            yield final


handler = DocxHandler()
