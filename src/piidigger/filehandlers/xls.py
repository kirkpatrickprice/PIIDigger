import logging
from logging.handlers import QueueHandler
from collections.abc import Iterator

import xlrd

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import (
    excelBlankColLimit, 
    excelBlankRowLimit, 
    maxChunkSize, 
    defaultChunkCount,
    )

# Each filehandler must have the following:
#   "handles" -     dictionary to identify lists of file extensions and mime types that the handler will manage.
#                   This will be read by globals upon initial load to build the full list of supported mime types and file extensions
#   "processFile" - Function that manages opening and reading of the file.  The main module will call this handler wtih the "processFile(filename)" function.
#                   processFile should provide the lines of text to each of the dataHandlers

handles={
    'ext': [
        '.xls',
    ],
    'mime': [
        'application/vnd.ms-excel',
        'application/excel',
        ],
}

def readFile(filename: str, 
                logConfig: dict,
                maxChunkCount = defaultChunkCount,
            ) -> Iterator[str]:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.  
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by processFile.
    '''

    logger=logging.getLogger('xls-handler')
    if not logger.handlers:
        logger.addHandler(QueueHandler(logConfig['q']))
    logger.setLevel(logConfig['level'])
    logger.propagate=False
    
    try:
        # Don't use "on_demand" in order to keep the code simpler.  All worksheets are loaded into RAM.

        book=xlrd.open_workbook(filename, on_demand=True, formatting_info=False,)
        logger.debug('%s: Read %d worksheets', filename, len(book.sheet_names()))
        for sheet in book.sheet_names():
            logger.debug('Processing worksheet: %s', str(sheet))
            activeSheet=book.sheet_by_name(sheet)
            handler: ContentHandler = ContentHandler(maxContentSize = maxChunkSize * maxChunkCount)
            blankRowCount=0
            rowCount=0
            totalRows=activeSheet.nrows
            totalCols=activeSheet.ncols
            # create a string with all of the content of this sheet
            # Iterate through each cell in each row.  If we reach a limit of blank cells, move to the next row
            # If we reach a limit of blank rows, then move to the next sheet.
            for row in range(totalRows):
                logger.debug('%s[Sheet %s]: Processing row [%d]', filename, sheet, rowCount)
                line: str = ''
                rowCount+=1
                rowHasData=False
                blankColCount=0
                for col in range(totalCols):
                    item=activeSheet.cell_value(row, col)
                    if item is None or item == '':
                        blankColCount+=1
                        if blankColCount > excelBlankColLimit:
                            break
                        continue
                    # xlrd converts all numbers to floats.  If the float is really an integer (ends in '.0'), convert it to a string without the decimal point
                    if type(item) == float and str(item)[-2:] == '.0':
                        item = str(item)[:-2]
                    line += str(item) + ' '
                    rowHasData=True
                handler.appendContent(line)
                if rowHasData:
                    blankRowCount=0
                else:
                    blankRowCount+=1
                    if blankRowCount > excelBlankRowLimit:
                        logger.debug('%s[Sheet %s]: Blank row count exceeded at row %d', filename, sheet, rowCount)
                        break
                if handler.contentBufferFull():
                    yield handler.getContent()
            book.unload_sheet(sheet)
            logger.debug('%s[Sheet %s]: Read content (%d bytes)', filename, sheet, handler.totalBytes)
            yield handler.finalizeContent()
            
        book.release_resources()
    except FileNotFoundError:
        logger.error('Previously discovered file no longer exists: %s. File skipped', filename)
    except PermissionError as e:
        logger.error('PermissionError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('OSError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except xlrd.compdoc.CompDocError:
        logger.error('Excel document corrupted: %s', filename,)
    except xlrd.biffh.XLRDError:
        logger.error('Excel read error: %s', filename,)
    except Exception as e:
        logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))

    

