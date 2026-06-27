import logging
from collections.abc import Iterator

from pypdf import PdfReader
from pypdf.errors import (
    EmptyFileError,
    PdfReadError,
)

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import DEFAULT_CHUNK_COUNT, MAX_CHUNK_SIZE
from piidigger.logmanager import LogManager

HANDLES = {
    'ext': [
        '.pdf',
    ],
    'mime': [
        'application/pdf',
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

    pd_logger = log_manager.getLogger('pdf_handler')

    try:
        # Read the PDF file
        # NOTE: PDF files are optimized for printing, not for text extraction.  This is a best-effort attempt to extract text from the PDF.
        #       It is not guaranteed to be accurate or complete.

        # the main pd_logger disables propagation to avoid duplicate message, which affects how PyPDF handles logging.
        # This is a workaround for the PyPDF library to avoid printing PyPDF warnings to the console.
        # All meaningful messages are logged to the central logger through pd_logger.
        logging.getLogger("pypdf").setLevel(logging.ERROR)

        document = PdfReader(filename, strict=False)
        pd_logger.debug('%s: Found %d pages', filename, len(document.pages))
        i: int = 0
        bytes_read: int = 0
        for i, page in enumerate(document.pages):
            bytes_read = 0
            pd_logger.debug('%s: Processing page: %s', filename, str(i))
            handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * max_chunk_count)
            # create a string with all of the content of this page
            page_content = page.extract_text()
            # split the content into lines
            for line in page_content.split('\n'):
                handler.append_content(line)
                bytes_read += len(line)
                if handler.content_buffer_full():
                    yield handler.get_content()

        # Read the metadata from the PDF file
        # NOTE: This is not guaranteed to be accurate or complete.
        metadata = document.metadata
        for key in metadata.keys():
            metadata_content: str = metadata.get(key)
            bytes_read += len(metadata_content)
            handler.append_content(metadata_content)
            if handler.content_buffer_full():
                yield handler.get_content()

        # Log the details and flush the handler buffer
        pd_logger.debug('%s[Page %s]: Read content (%d bytes)', filename, i, bytes_read)
        yield handler.finalize_content()

    except FileNotFoundError:
        pd_logger.error('Previously discovered file no longer exists: %s. File skipped', filename)
    except PermissionError as e:
        pd_logger.error('PermissionError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        pd_logger.error('OSError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except Warning as e:
        pd_logger.error('%s: %s', filename, e)
    except EmptyFileError as e:
        pd_logger.error('EmptyFileError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except PdfReadError as e:
        pd_logger.error('PdfReadError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except Exception as e:
        pd_logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))
    else:
        document.close()


# ---------------------------------------------------------------------------
# 2.0 FileHandler protocol implementation
# ---------------------------------------------------------------------------

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
                for line in page_content.split('\n'):
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

        except (EmptyFileError, PdfReadError):
            return
        finally:
            stream.close()


handler = PdfHandler()
