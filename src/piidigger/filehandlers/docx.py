'''Process DOCX files'''

import logging
import warnings
from logging.handlers import QueueHandler

from docx2python import docx2python

# Only need to modify the path during unit testing of this file.
if __name__=='__main__':
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent / ''))

import piidigger.globalfuncs as globalfuncs

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

def processFile(filename: str, dataHandlers: list, logConfig: dict) -> list:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.  
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by processFile.
    '''

    logger=logging.getLogger('docx-handler')
    if not logger.handlers:
        logger.addHandler(QueueHandler(logConfig['q']))
    logger.setLevel(logConfig['level'])
    logger.propagate=False

    results={
        'filename': filename,
        'matches': dict()
    }

    try:
        # Read in all of the docx content and close the file
        docxContent=docx2python(filename)

        # Manipulate the text to get everything into one continuous string (space-separated)
        content=' '.join(docxContent.text.split('\n')).replace('\t', ' ') + ' ' + str(docxContent.core_properties)
        logger.debug('%s: Read %d bytes', filename, len(content))

        # Close the file now that we're done with it.
        docxContent.close()

        # Break up the text into bite-sized chunks for regex processing
        chunks=globalfuncs.makeChunks(content)
        logger.debug('%s: Created %d chunks', filename, len(chunks))

        del content

        for handler in dataHandlers:
            logger.debug('%s: Processing %d chunks with %s', filename, len(chunks), handler.dhName)
            for chunk in chunks:
                results=globalfuncs.processMatches(results, handler.findMatch(chunk), handler.dhName)
    except FileNotFoundError:
        logger.error('Previously discovered file no longer exists: %s. File skipped', filename)
    except PermissionError as e:
        logger.error('PermissionError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('OSError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except Exception as e:
        logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))
        
    
    # Since Python sets aren't serializable as a JSON object type, we'll convert our results to Lists now. 
    logger.debug('%s: Rebuilding result sets into lists', filename)                   
    for handler in results['matches']:
        for key in results['matches'][handler]:
            l=list(results['matches'][handler][key])
            results['matches'][handler][key]=l

    logger.debug('%s: Processing complete', filename)
    return results


def main():
    from sys import argv
    from multiprocessing import Queue
    handlers=globalfuncs.getAllDataHandlerModules()
    logConfig={'q': Queue(),
                'level': "DEBUG",
                }
    for arg in argv[1:]:

        print(processFile(arg, handlers, logConfig))

if __name__ == '__main__':
    main()

