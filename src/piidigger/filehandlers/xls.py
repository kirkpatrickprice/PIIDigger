from collections.abc import Iterator

import xlrd

from piidigger.filehandlers._sharedfuncs import ContentBuffer
from piidigger.models.config import Config

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

    Preferred path (archive members): source.open_bytes() returns bytes which
    are passed directly to xlrd via the file_contents parameter — no temp file.

    Fallback path (on-disk files): source.open_bytes() returns None, so
    source.materialize() is called to obtain a filesystem path.  For
    FilesystemItem materialize() is a no-op (returns the path itself).
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        data = source.open_bytes()
        if data is not None:
            book = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=False)
        else:
            book = xlrd.open_workbook(str(source.materialize()), on_demand=True, formatting_info=False)
        try:
            for sheet_name in book.sheet_names():
                active_sheet = book.sheet_by_name(sheet_name)
                content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)
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
                            if blank_col_count > config.spreadsheet.blank_col_limit:
                                break
                            continue
                        if isinstance(item, float) and str(item)[-2:] == ".0":
                            item = str(item)[:-2]
                        line += str(item) + " "
                        row_has_data = True

                    content_buffer.append_content(line)
                    if row_has_data:
                        blank_row_count = 0
                    else:
                        blank_row_count += 1
                        if blank_row_count > config.spreadsheet.blank_row_limit:
                            break

                    if content_buffer.content_buffer_full():
                        yield content_buffer.get_content()

                book.unload_sheet(sheet_name)
                final = content_buffer.finalize_content()
                if final:
                    yield final
        finally:
            book.release_resources()


handler = XlsHandler()
