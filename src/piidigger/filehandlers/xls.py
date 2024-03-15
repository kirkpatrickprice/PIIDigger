import logging
import xlrd
from logging.handlers import QueueHandler

# Only need to modify the path during unit testing of this file.
if __name__=='__main__':
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent / ''))

import piidigger.globalfuncs as globalfuncs

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

def processFile(filename: str, dataHandlers: list, logConfig: dict) -> list:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.  
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by processFile.
    '''

    logger=logging.getLogger('xls-handler')
    if not logger.handlers:
        logger.addHandler(QueueHandler(logConfig['q']))
    logger.setLevel(logConfig['level'])
    logger.propagate=False
    
    results={
        'filename': filename,
        'matches': dict()
    }

    try:
        # Don't use "on_demand" in order to keep the code simpler.  All worksheets are loaded into RAM.

        book=xlrd.open_workbook(filename, on_demand=False, formatting_info=False,)
        logger.debug('%s: Read %d worksheets', filename, len(book.sheet_names()))
        for sheet in book.sheet_names():
            logger.debug('Processing worksheet: %s', str(sheet))
            activeSheet=book.sheet_by_name(sheet)
            content=str()
            blankRowCount=0
            rowCount=0
            totalRows=activeSheet.nrows
            totalCols=activeSheet.ncols
            # create a string with all of the content of this sheet
            # Iterate through each cell in each row.  If we reach a limit of blank cells, move to the next row
            # If we reach a limit of blank rows, then move to the next sheet.
            for row in range(totalRows):
                rowCount+=1
                logger.debug('%s[Sheet %s]: Processing row [%d]', filename, sheet, rowCount)
                rowHasData=False
                blankColCount=0
                for col in range(totalCols):
                    item=activeSheet.cell_value(row, col)
                    if item is None or item == '':
                        blankColCount+=1
                        if blankColCount>globalfuncs.excelBlankColLimit:
                            break
                        continue
                    content += str(item) + ' '
                    rowHasData=True
                if rowHasData:
                    blankRowCount=0
                    content += ' '
                else:
                    blankRowCount+=1
                    if blankRowCount>globalfuncs.excelBlankRowLimit:
                        logger.debug('%s[Sheet %s]: Blank row count exceeded at row %d', filename, sheet, rowCount)
                        break

            logger.debug('%s[Sheet %s]: Read content (%d bytes)', filename, sheet, len(content))
            chunks = globalfuncs.makeChunks(content)
            logger.debug('%s[Sheet %s]: Created %d chunks', filename, sheet, len(chunks))

            del content

            for handler in dataHandlers:
                logger.debug('%s[Sheet %s]: Processing %d chunks with %s', filename, sheet, len(chunks), handler.dhName)
                for chunk in chunks:
                    results=globalfuncs.processMatches(results, handler.findMatch(chunk), handler.dhName)
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

