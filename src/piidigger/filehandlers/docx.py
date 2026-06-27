'''Process DOCX files'''

import warnings
from collections.abc import Iterator

from docx2python import docx2python
from docx2python.iterators import iter_paragraphs

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import DEFAULT_CHUNK_COUNT, MAX_CHUNK_SIZE
from piidigger.logmanager import LogManager

warnings.filterwarnings('ignore', category=UserWarning, module='docx2python')

HANDLES = {
    'ext': [
        '.docx',
    ],
    'mime': [
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    ],
}


def read_file(filename: str,
              log_manager: LogManager,
              max_chunk_count: int = DEFAULT_CHUNK_COUNT,
              ) -> Iterator[str]:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by read_file.
    '''

    logger = log_manager.getLogger('docx_handler')

    try:
        # Read in all of the docx content and close the file
        docx_content = docx2python(filename)
        handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * max_chunk_count)

        # This will iterate over the header, body and footer of the document, including all of the text and tables
        for line in iter_paragraphs(docx_content.document):
            handler.append_content(line)
            if handler.content_buffer_full():
                yield handler.get_content()

        for comment in docx_content.comments:
            if comment is not None:
                handler.append_content(comment[3])
                if handler.content_buffer_full():
                    yield handler.get_content()

        # No size check -- we'll just append the properties to the end of the content and send it
        handler.append_content(str(docx_content.core_properties))

        # Once we've processed the entire file, it's time to send that last bit of info that hasn't already been sent.
        logger.debug('%s: Read %d lines', filename, handler.total_bytes)

        # Return the last chunk of content
        yield handler.finalize_content()

    except FileNotFoundError:
        logger.error('%s: Previously discovered file no longer exists. File skipped', filename)
    except PermissionError as e:
        logger.error('%s: PermissionError.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('%s: OSError.  File skipped.  Error message: %s', filename, str(e))
    except Exception as e:
        logger.error('%s: Unknown exception.  File skipped.  Error message: %s', filename, str(e))


# ---------------------------------------------------------------------------
# 2.0 FileHandler protocol implementation
# ---------------------------------------------------------------------------

handles = HANDLES


class DocxHandler:
    """FileHandler for DOCX files.

    Uses source.materialize() to get a real filesystem path because
    docx2python does not accept a stream — it requires an openable file path.
    For FilesystemItem this is a no-op (returns the path itself).
    """

    def read(self, source) -> Iterator[str]:  # source: ScannableItem
        path = source.materialize()
        chunk_handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * DEFAULT_CHUNK_COUNT)

        docx_content = docx2python(str(path))

        for line in iter_paragraphs(docx_content.document):
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
