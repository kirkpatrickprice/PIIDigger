"""Process DOCX files"""

import warnings
from collections.abc import Iterator
from io import BytesIO

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

# DocxContent properties holding the document's text, in the order docx2python's
# own .document property concatenates them.
_TEXT_PARTS = ("header", "body", "footer", "footnotes", "endnotes")


def _joined(parts: list[str]) -> str:
    """Parts as a phrase: "a", "a and b", "a, b and c"."""
    return " and ".join(filter(None, (", ".join(parts[:-1]), parts[-1])))


class DocxHandler:
    """FileHandler for DOCX files.

    Preferred path (archive members): source.open_bytes() returns bytes which
    are wrapped in BytesIO and passed directly to docx2python — no temp file.

    Fallback path (on-disk files): source.open_bytes() returns None, so
    source.materialize() is called to obtain a filesystem path.  For
    FilesystemItem materialize() is a no-op (returns the path itself).

    docx2python opens the underlying ZipFile lazily and holds it open until
    DocxContent.close() is called, so close() runs in a finally block.

    Each part of the document is read on its own, so a bad CRC or malformed XML
    in a footnote does not cost the body.  What could not be read goes to
    source.report_unread().  An OSError is not damage, so it still reaches the
    worker: access denied, or a file removed after it was listed.  Damage to a
    .rels file stops docx2python reading any part at all, and is reported the
    same way as a file that is not a real docx.  Only an empty file is silent,
    because it holds nothing to miss.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        data = source.open_bytes()
        docx_arg: BytesIO | str = BytesIO(data) if data is not None else str(source.materialize())
        docx_content = docx2python(docx_arg)
        try:
            if source.size == 0:
                return
            content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)

            unread: list[str] = []
            text_read = False
            for part in _TEXT_PARTS:
                try:
                    paragraphs = list(iter_paragraphs(getattr(docx_content, part)))
                except OSError:
                    raise
                except Exception:  # noqa: BLE001
                    unread.append(part)
                    continue
                for line in paragraphs:
                    text_read = text_read or bool(line.strip())
                    content_buffer.append_content(line)
                    if content_buffer.content_buffer_full():
                        yield content_buffer.get_content()

            text_part_unread = bool(unread)
            if len(unread) == len(_TEXT_PARTS):
                source.report_unread("Word document could not be read", nothing_read=True)
                return

            try:
                comments = docx_content.comments
            # Any failure here, from a comment with no w:date to a bad CRC or
            # malformed XML, is reported rather than costing the body text,
            # which may still only be buffered at this point.
            except Exception:  # noqa: BLE001
                comments = []
                unread.append("comments")
            for comment in comments:
                if comment is not None:
                    text_read = text_read or bool(comment[3].strip())
                    content_buffer.append_content(comment[3])
                    if content_buffer.content_buffer_full():
                        yield content_buffer.get_content()

            try:
                content_buffer.append_content(str(docx_content.core_properties))
            except Exception:  # noqa: BLE001
                unread.append("document properties")

            if unread:
                source.report_unread(
                    f"Word {_joined(unread)} could not be read", nothing_read=text_part_unread and not text_read
                )

            final = content_buffer.finalize_content()
            if final:
                yield final

        finally:
            docx_content.close()


handler = DocxHandler()
