from collections.abc import Iterator

import xlrd

from piidigger.filehandlers._constants import (
    DEFAULT_CHUNK_COUNT,
    EXCEL_BLANK_COL_LIMIT,
    EXCEL_BLANK_ROW_LIMIT,
    MAX_CHUNK_SIZE,
)
from piidigger.filehandlers._sharedfuncs import ContentHandler

HANDLES = {
    "ext": [
        ".xls",
    ],
    "mime": [
        "application/vnd.ms-excel",
        "application/excel",
    ],
}

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
                    line = ""
                    row_has_data = False
                    blank_col_count = 0

                    for col in range(total_cols):
                        item = active_sheet.cell_value(row, col)
                        if item is None or item == "":
                            blank_col_count += 1
                            if blank_col_count > EXCEL_BLANK_COL_LIMIT:
                                break
                            continue
                        if isinstance(item, float) and str(item)[-2:] == ".0":
                            item = str(item)[:-2]
                        line += str(item) + " "
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
