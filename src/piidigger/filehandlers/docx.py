'''Process DOCX files'''

import logging
import warnings
from logging.handlers import QueueHandler

from docx2python import docx2python

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

def readFile(filename: str, logConfig: dict) -> list:
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

    try:
        # Read in all of the docx content and close the file
        docxContent=docx2python(filename)

        # Manipulate the text to get everything into one continuous string (space-separated)
        content=' '.join(docxContent.text.split('\n')).replace('\t', ' ') + ' ' + str(docxContent.core_properties)
        logger.debug('%s: Read %d bytes', filename, len(content))

        # Close the file now that we're done with it.
        docxContent.close()
        
    except FileNotFoundError:
        logger.error('%s: Previously discovered file no longer exists. File skipped', filename)
    except PermissionError as e:
        logger.error('%s: PermissionError.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('%s: OSError.  File skipped.  Error message: %s', filename, str(e))
    except Exception as e:
        logger.error('%s: Unknown exception.  File skipped.  Error message: %s', filename, str(e))
    
    return [content]

