"""Process DOCX files"""

import warnings
from collections.abc import Iterator
from io import BytesIO
from zipfile import BadZipFile

from docx2python import docx2python
from docx2python.iterators import iter_paragraphs

from piidigger.filehandlers._sharedfuncs import ContentBuffer
from piidigger.models.config import Config

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

    docx2python opens the underlying ZipFile lazily and holds it open until
    DocxContent.close() is called, so close() runs in a finally block.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        data = source.open_bytes()
        docx_arg: BytesIO | str = BytesIO(data) if data is not None else str(source.materialize())
        docx_content = docx2python(docx_arg)
        try:
            content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)

            # .document is a lazy property that opens the ZIP — catch corruption here
            for line in iter_paragraphs(docx_content.document):
                content_buffer.append_content(line)
                if content_buffer.content_buffer_full():
                    yield content_buffer.get_content()

            unread: list[str] = []
            try:
                comments = docx_content.comments
            # Any failure here, from a comment with no w:date to a bad CRC or
            # malformed XML, is reported rather than costing the body text,
            # which is still only buffered at this point.
            except Exception:  # noqa: BLE001
                comments = []
                unread.append("comments")
            for comment in comments:
                if comment is not None:
                    content_buffer.append_content(comment[3])
                    if content_buffer.content_buffer_full():
                        yield content_buffer.get_content()

            try:
                content_buffer.append_content(str(docx_content.core_properties))
            except Exception:  # noqa: BLE001
                unread.append("document properties")

            if unread:
                source.report_unread(f"Word {' and '.join(unread)} could not be read")

            final = content_buffer.finalize_content()
            if final:
                yield final

        except BadZipFile:
            return
        finally:
            docx_content.close()


handler = DocxHandler()
