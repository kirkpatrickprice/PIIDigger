'''Process DOCX files'''

import logging
import warnings
from logging.handlers import QueueHandler

from docx2python import docx2python
from docx2python.iterators import iter_paragraphs

from piidigger.globalvars import maxChunkSize
from piidigger.globalfuncs import appendContent

warnings.filterwarnings('ignore', category=UserWarning, module='docx2python')

# Each filehandler must have the following:
#   "handles" -     dictionary to identify lists of file extensions and mime types that the handler will manage.
#                   This will be read by globals upon initial load to build the full list of supported mime types and file extensions
#   "processFile" - Function that manages opening and reading of the file.  The main module will call this handler wtih the "processFile(filename)" function.
#                   processFile should provide the lines of text to each of the dataHandlers

handles={
    'ext': [
        '.docx',
    ],
    'mime': [
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        ],
}

def readFile(filename: str, 
             logConfig: dict,
             maxChunkCount = 100_000):
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.  
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by processFile.
    '''

    logger=logging.getLogger('docx-handler')
    if not logger.handlers:
        logger.addHandler(QueueHandler(logConfig['q']))
    logger.setLevel(logConfig['level'])
    logger.propagate=False
    content: str = ''
    totalBytes: int = 0
    maxContentSize = maxChunkSize * maxChunkCount

    try:
        # Read in all of the docx content and close the file
        docxContent=docx2python(filename)

        # This will iterate of the header, body and footer of the document, including all of the text and tables
        for line in iter_paragraphs(docxContent.document):
            content, unused = appendContent(content, line, maxContentSize)
            if len(content.strip()) > maxContentSize:
                totalBytes += len(content.strip())
                yield content.strip()
                content = unused

        for comment in docxContent.comments:
            if not comment is None:
                content, unused = appendContent(content, comment[3], maxContentSize)
                if len(content.strip()) > maxContentSize:
                    totalBytes += len(content.strip())
                    yield content.strip()
                    content = unused

        # No size check -- we'll just append the properties to the end of the content and send it
        content += ' ' + str(docxContent.properties).replace('\t', ' ').strip()

    # Once we've processed the entire file, it's time to send that last bit of info that hasn't already been sent.
        totalBytes += len(content.strip())
        logger.debug('%s: Read %d lines', filename, totalBytes)

        # Return the last chunk of content    
        yield content.strip()
        
    except FileNotFoundError:
        logger.error('%s: Previously discovered file no longer exists. File skipped', filename)
    except PermissionError as e:
        logger.error('%s: PermissionError.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('%s: OSError.  File skipped.  Error message: %s', filename, str(e))
    except Exception as e:
        logger.error('%s: Unknown exception.  File skipped.  Error message: %s', filename, str(e))
    

