import logging
import openpyxl
import warnings
from logging.handlers import QueueHandler

from openpyxl.utils.exceptions import *
from zipfile import BadZipFile

# Only need to modify the path during unit testing of this file.
if __name__=='__main__':
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent / ''))

import piidigger.globalfuncs as globalfuncs

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

def processFile(filename: str, dataHandlers: list, logConfig: dict) -> list:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.  
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by processFile.
    '''

    logger=logging.getLogger('xlsx-handler')
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
        # Some spreadsheet dimensions can't be accurately determined -- e.g. if there's a lot of extraneous formatting to make it look "pretty"
        # We build a safety valve so that it stops after the first 5000 rows and 5000 columns.  If the "interesting data" is present outside of these limits...
        # well... it's probably not the ONLY instance of such data.

        book=openpyxl.load_workbook(filename=filename, read_only=True, data_only=True,)
        logger.debug('%s: Read %d worksheets', filename, len(book.sheetnames))
        for sheet in book.sheetnames:
            logger.debug('%s: Processing worksheet: %s', filename, str(sheet))
            activeSheet=book[sheet]
            content=str()
            blankRowCount=0
            rowCount=0
            # create a string with all of the content of this sheet
            # Iterate through each cell in each row.  If we reach a limit of blank cells, move to the next row
            # If we reach a limit of blank rows, then move to the next sheet.
            for row in activeSheet.iter_rows(values_only=True):
                rowCount+=1
                rowHasData=False
                blankColCount=0
                for item in row:
                    if isinstance(item, openpyxl.cell.cell.MergedCell):
                        continue
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
        book.close()
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

