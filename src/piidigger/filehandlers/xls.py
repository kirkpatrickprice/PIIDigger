from collections.abc import Iterator

import xlrd
import xlrd.biffh
import xlrd.compdoc

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import DEFAULT_CHUNK_COUNT, EXCEL_BLANK_COL_LIMIT, EXCEL_BLANK_ROW_LIMIT, MAX_CHUNK_SIZE
from piidigger.logmanager import LogManager

HANDLES = {
    'ext': [
        '.xls',
    ],
    'mime': [
        'application/vnd.ms-excel',
        'application/excel',
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

    logger = log_manager.getLogger('xls-handler')

    try:
        # Don't use "on_demand" in order to keep the code simpler.  All worksheets are loaded into RAM.

        book = xlrd.open_workbook(filename, on_demand=True, formatting_info=False)
        logger.debug('%s: Read %d worksheets', filename, len(book.sheet_names()))
        for sheet in book.sheet_names():
            logger.debug('Processing worksheet: %s', str(sheet))
            active_sheet = book.sheet_by_name(sheet)
            handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * max_chunk_count)
            blank_row_count = 0
            row_count = 0
            total_rows = active_sheet.nrows
            total_cols = active_sheet.ncols
            # create a string with all of the content of this sheet
            # Iterate through each cell in each row.  If we reach a limit of blank cells, move to the next row
            # If we reach a limit of blank rows, then move to the next sheet.
            for row in range(total_rows):
                logger.debug('%s[Sheet %s]: Processing row [%d]', filename, sheet, row_count)
                line: str = ''
                row_count += 1
                row_has_data = False
                blank_col_count = 0
                for col in range(total_cols):
                    item = active_sheet.cell_value(row, col)
                    if item is None or item == '':
                        blank_col_count += 1
                        if blank_col_count > EXCEL_BLANK_COL_LIMIT:
                            break
                        continue
                    # xlrd converts all numbers to floats.  If the float is really an integer (ends in '.0'), convert it to a string without the decimal point
                    if isinstance(item, float) and str(item)[-2:] == '.0':
                        item = str(item)[:-2]
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
            book.unload_sheet(sheet)
            logger.debug('%s[Sheet %s]: Read content (%d bytes)', filename, sheet, handler.total_bytes)
            yield handler.finalize_content()

        book.release_resources()
    except FileNotFoundError:
        logger.error('Previously discovered file no longer exists: %s. File skipped', filename)
    except PermissionError as e:
        logger.error('PermissionError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except OSError as e:
        logger.error('OSError adding %s.  File skipped.  Error message: %s', filename, str(e))
    except xlrd.compdoc.CompDocError:
        logger.error('Excel document corrupted: %s', filename)
    except xlrd.biffh.XLRDError:
        logger.error('Excel read error: %s', filename)
    except Exception as e:
        logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))


# ---------------------------------------------------------------------------
# 2.0 FileHandler protocol implementation
# ---------------------------------------------------------------------------

handles = HANDLES


class XlsHandler:
    """FileHandler for legacy XLS files.

    Uses source.materialize() to get a real filesystem path because xlrd's
    open_workbook() requires a filename string.
    For FilesystemItem this is a no-op (returns the path itself).
    """

    def read(self, source) -> Iterator[str]:  # source: ScannableItem
        path = source.materialize()
        book = xlrd.open_workbook(str(path), on_demand=True, formatting_info=False)
        try:
            for sheet_name in book.sheet_names():
                active_sheet = book.sheet_by_name(sheet_name)
                chunk_handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * DEFAULT_CHUNK_COUNT)
                blank_row_count = 0
                row_count = 0
                total_rows = active_sheet.nrows
                total_cols = active_sheet.ncols

                for row in range(total_rows):
                    row_count += 1
                    line = ''
                    row_has_data = False
                    blank_col_count = 0

                    for col in range(total_cols):
                        item = active_sheet.cell_value(row, col)
                        if item is None or item == '':
                            blank_col_count += 1
                            if blank_col_count > EXCEL_BLANK_COL_LIMIT:
                                break
                            continue
                        if isinstance(item, float) and str(item)[-2:] == '.0':
                            item = str(item)[:-2]
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

                book.unload_sheet(sheet_name)
                final = chunk_handler.finalize_content()
                if final:
                    yield final
        finally:
            book.release_resources()


handler = XlsHandler()
