import warnings
from pypdf import PdfReader
from pypdf.errors import (
    PdfReadError, 
    EmptyFileError,
    )
from collections.abc import Iterator

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import maxChunkSize
from piidigger.globalvars import defaultChunkCount
from piidigger.logmanager import LogManager

# Ignore the UserWarning message from OpenPyXL that seem to pop up here and there
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

# Each filehandler must have the following:
#   "handles" -     dictionary to identify lists of file extensions and mime types that the handler will manage.
#                   This will be read by globals upon initial load to build the full list of supported mime types and file extensions
#   "processFile" - Function that manages opening and reading of the file.  The main module will call this handler wtih the "processFile(filename)" function.
#                   processFile should provide the lines of text to each of the dataHandlers

handles={
    'ext': [
        '.pdf',
    ],
    'mime': [
        'application/pdf',
        ],
}

def readFile(filename: str, 
             logManager: LogManager,
             maxChunkCount: int = defaultChunkCount,
            ) -> Iterator[str]:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.  
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by processFile.
    '''

    logger = logManager.getLogger('pdf_handler')

    content: str = ''
    totalBytes: int = 0
    maxContentSize = maxChunkSize * maxChunkCount

    
    try:
        # Read the PDF file
        # NOTE: PDF files are optimized for printing, not for text extraction.  This is a best-effort attempt to extract text from the PDF.
        #       It is not guaranteed to be accurate or complete.

        document=PdfReader(filename)
        logger.debug('%s: Found %d pages', filename, len(document.pages))
        i: int = 0
        bytes_read: int = 0
        for i, page in enumerate(document.pages):
            bytes_read = 0
            logger.debug('%s: Processing page: %s', filename, str(i))
            handler: ContentHandler = ContentHandler(maxContentSize = maxChunkSize * maxChunkCount)
            # create a string with all of the content of this page
            page_content = page.extract_text()
            # split the content into lines
            for line in page_content.split('\n'):
                handler.appendContent(line)
                bytes_read += len(line)
                if handler.contentBufferFull():
                    yield handler.getContent()

        # Read the metadata from the PDF file
        # NOTE: This is not guaranteed to be accurate or complete.        
        metadata = document.metadata
        for key in metadata.keys():
            metadata_content: str = metadata.get(key)
            bytes_read += len(metadata_content)
            handler.appendContent(metadata_content)
            if handler.contentBufferFull():
                yield handler.getContent()

        # Log the details and flush the handler buffer
        logger.debug('%s[Page %s]: Read content (%d bytes)', filename, i, bytes_read)
        yield handler.finalizeContent()
            
    except FileNotFoundError:
        logger.error('Previously discovered file no longer exists: %s. File skipped', filename)
    except PermissionError as e:
        logger.error('PermissionError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('OSError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except UserWarning as e:
        logger.error('%s: %s', filename, e)
    except EmptyFileError as e:
        logger.error('EmptyFileError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except PdfReadError as e:
        logger.error('PdfReadError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except Exception as e:
        logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))
    else:
        document.close()



