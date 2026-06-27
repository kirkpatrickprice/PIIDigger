import warnings
from collections.abc import Iterator
from zipfile import BadZipFile

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.utils.exceptions import (
    CellCoordinatesException,
    IllegalCharacterError,
    InvalidFileException,
    SheetTitleException,
)

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import DEFAULT_CHUNK_COUNT, EXCEL_BLANK_COL_LIMIT, EXCEL_BLANK_ROW_LIMIT, MAX_CHUNK_SIZE
from piidigger.logmanager import LogManager

# Ignore the UserWarning message from OpenPyXL that seem to pop up here and there
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

HANDLES = {
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


def read_file(filename: str,
              log_manager: LogManager,
              max_chunk_count: int = DEFAULT_CHUNK_COUNT,
              ) -> Iterator[str]:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a list of results that have been validated by each datahandler.
    "filename" is a string of the path and filename to process.  "handlers" is passed as a list of module objects that are called directly by read_file.
    '''

    logger = log_manager.getLogger('xlsx_handler')

    content: str = ''

    try:
        # Don't use "on_demand" in order to keep the code simpler.  All worksheets are loaded into RAM.
        # Some spreadsheet dimensions can't be accurately determined -- e.g. if there's a lot of extraneous formatting to make it look "pretty"
        # We build a safety valve so that it stops after the first 5000 rows and 5000 columns.  If the "interesting data" is present outside of these limits...
        # well... it's probably not the ONLY instance of such data.

        book = openpyxl.load_workbook(filename=filename, read_only=True, data_only=True)
        logger.debug('%s: Read %d worksheets', filename, len(book.sheetnames))
        for sheet in book.sheetnames:
            logger.debug('%s: Processing worksheet: %s', filename, str(sheet))
            active_sheet = book[sheet]
            handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * max_chunk_count)
            blank_row_count = 0
            row_count = 0
            # create a string with all of the content of this sheet
            # Iterate through each cell in each row.  If we reach a limit of blank cells, move to the next row
            # If we reach a limit of blank rows, then move to the next sheet.
            for row in active_sheet.iter_rows(values_only=True):
                row_count += 1
                row_has_data = False
                line: str = ''
                blank_col_count: int = 0
                for item in row:
                    if isinstance(item, MergedCell):
                        continue
                    if item is None or item == '':
                        blank_col_count += 1
                        if blank_col_count > EXCEL_BLANK_COL_LIMIT:
                            break
                        continue
                    line += str(item) + ' '
                    row_has_data = True
                handler.append_content(line)

                if row_has_data:
                    blank_row_count = 0
                else:
                    blank_row_count += 1
                    if blank_row_count > EXCEL_BLANK_ROW_LIMIT:
                        logger.debug('%s[Sheet %s]: Blank row count exceeded at row %d', filename, sheet, row_count)
                        break
                if handler.content_buffer_full():
                    yield handler.get_content()

            logger.debug('%s[Sheet %s]: Read content (%d bytes)', filename, sheet, len(content))
            yield handler.finalize_content()

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


# ---------------------------------------------------------------------------
# 2.0 FileHandler protocol implementation
# ---------------------------------------------------------------------------

handles = HANDLES


class XlsxHandler:
    """FileHandler for XLSX/XLSM/XLTM files.

    Uses source.materialize() to get a real filesystem path because openpyxl's
    load_workbook() does not reliably accept seekable streams in read_only mode.
    For FilesystemItem this is a no-op (returns the path itself).
    """

    def read(self, source) -> Iterator[str]:  # source: ScannableItem
        path = source.materialize()
        book = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
        try:
            for sheet_name in book.sheetnames:
                active_sheet = book[sheet_name]
                chunk_handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * DEFAULT_CHUNK_COUNT)
                blank_row_count = 0
                row_count = 0

                for row in active_sheet.iter_rows(values_only=True):
                    row_count += 1
                    row_has_data = False
                    line = ''
                    blank_col_count = 0

                    for item in row:
                        if isinstance(item, MergedCell):
                            continue
                        if item is None or item == '':
                            blank_col_count += 1
                            if blank_col_count > EXCEL_BLANK_COL_LIMIT:
                                break
                            continue
                        line += str(item) + ' '
                        row_has_data = True

                    chunk_handler.append_content(line)
                    if row_has_data:
                        blank_row_count = 0
                    else:
                        blank_row_count += 1
                        if blank_row_count > EXCEL_BLANK_ROW_LIMIT:
                            break

                    if chunk_handler.content_buffer_full():
                        yield chunk_handler.get_content()

                final = chunk_handler.finalize_content()
                if final:
                    yield final
        finally:
            book.close()


handler = XlsxHandler()
