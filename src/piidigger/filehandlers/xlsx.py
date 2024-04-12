import logging
import openpyxl
import warnings
from logging.handlers import QueueHandler
from collections.abc import Iterator

from openpyxl.utils.exceptions import *
from zipfile import BadZipFile

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import (
    excelBlankColLimit, 
    excelBlankRowLimit, 
    maxChunkSize, 
    defaultChunkCount,
    )

# Ignore the UserWarning message from OpenPyXL that seem to pop up here and there
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

# Each filehandler must have the following:
#   "handles" -     dictionary to identify lists of file extensions and mime types that the handler will manage.
#                   This will be read by globals upon initial load to build the full list of supported mime types and file extensions
#   "processFile" - Function that manages opening and reading of the file.  The main module will call this handler wtih the "processFile(filename)" function.
#                   processFile should provide the lines of text to each of the dataHandlers

handles={
    'ext': [
        '.xlsx',
        '.xlsm',
        '.xlst',
        '.xltm',
    ],
    'mime': [
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel.sheet.macroEnabled',
        'application/vnd.ms-excel.template',
        ],
}

def readFile(filename: str, 
             logConfig: dict,
             maxChunkCount: int = defaultChunkCount,
            ) -> Iterator[str]:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.  
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by processFile.
    '''

    logger: logging = logging.getLogger('xlsx-handler')
    if not logger.handlers:
        logger.addHandler(QueueHandler(logConfig['q']))
    logger.setLevel(logConfig['level'])
    logger.propagate=False
    content: str = ''
    totalBytes: int = 0
    maxContentSize = maxChunkSize * maxChunkCount

    
    try:
        # Don't use "on_demand" in order to keep the code simpler.  All worksheets are loaded into RAM.
        # Some spreadsheet dimensions can't be accurately determined -- e.g. if there's a lot of extraneous formatting to make it look "pretty"
        # We build a safety valve so that it stops after the first 5000 rows and 5000 columns.  If the "interesting data" is present outside of these limits...
        # well... it's probably not the ONLY instance of such data.

        book=openpyxl.load_workbook(filename=filename, read_only=True, data_only=True,)
        logger.debug('%s: Read %d worksheets', filename, len(book.sheetnames))
        for sheet in book.sheetnames:
            logger.debug('%s: Processing worksheet: %s', filename, str(sheet))
            activeSheet=book[sheet]
            handler: ContentHandler = ContentHandler(maxContentSize = maxChunkSize * maxChunkCount)
            blankRowCount=0
            rowCount=0
            # create a string with all of the content of this sheet
            # Iterate through each cell in each row.  If we reach a limit of blank cells, move to the next row
            # If we reach a limit of blank rows, then move to the next sheet.
            for row in activeSheet.iter_rows(values_only=True):
                rowCount+=1
                rowHasData=False
                line: str = ''
                blankColCount: int = 0
                for item in row:
                    if isinstance(item, openpyxl.cell.cell.MergedCell):
                        continue
                    if item is None or item == '':
                        blankColCount+=1
                        if blankColCount>excelBlankColLimit:
                            break
                        continue
                    line += str(item) + ' '
                    rowHasData=True
                handler.appendContent(line)

                if rowHasData:
                    blankRowCount=0
                else:
                    blankRowCount+=1
                    if blankRowCount>excelBlankRowLimit:
                        logger.debug('%s[Sheet %s]: Blank row count exceeded at row %d', filename, sheet, rowCount)
                        break
                if handler.contentBufferFull():
                    yield handler.getContent()
                    
            logger.debug('%s[Sheet %s]: Read content (%d bytes)', filename, sheet, len(content))
            yield handler.finalizeContent()
            
    except FileNotFoundError:
        logger.error('Previously discovered file no longer exists: %s. File skipped', filename)
    except PermissionError as e:
        logger.error('PermissionError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('OSError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except CellCoordinatesException as e: 
        logger.error('%s: %s', filename, e)
    except IllegalCharacterError as e:
        logger.error('%s: %s', filename, e)
    except InvalidFileException as e:
        logger.error('%s: %s', filename, e)
    except SheetTitleException as e:
        logger.error('%s: %s', filename, e)
    except UserWarning as e:
        logger.error('%s: %s', filename, e)
    except BadZipFile as e:
        logger.error('%s: %s', filename, e)
    except Exception as e:
        logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))
    else:
        book.close()



