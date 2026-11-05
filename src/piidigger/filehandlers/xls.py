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

# xlrd has no exception type of its own for this.
_ENCRYPTED_MESSAGE = "Workbook is encrypted"


class XlsHandler:
    """FileHandler for legacy XLS files.

    Preferred path (archive members): source.open_bytes() returns bytes which
    are passed directly to xlrd via the file_contents parameter — no temp file.

    Fallback path (on-disk files): source.open_bytes() returns None, so
    source.materialize() is called to obtain a filesystem path.  For
    FilesystemItem materialize() is a no-op (returns the path itself).

    A workbook or sheet that cannot be read goes to source.report_unread(), naming
    only the exception type because xlrd's messages can quote the file.  Other
    sheets are still read.  An OSError still reaches the worker, and only an empty
    file is silent, because it holds nothing to miss.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        try:
            data = source.open_bytes()
            if data is not None:
                book = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=False)
            else:
                book = xlrd.open_workbook(str(source.materialize()), on_demand=True, formatting_info=False)
        except OSError:
            raise
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, xlrd.XLRDError) and str(exc) == _ENCRYPTED_MESSAGE:
                source.report_unread("Excel workbook is password protected", nothing_read=True)
            elif source.size:
                source.report_unread(f"Excel workbook could not be read: {type(exc).__name__}", nothing_read=True)
            return
        try:
            sheet_names = book.sheet_names()
            sheets_unread = 0
            first_unread = ""
            text_read = False
            for sheet_number, sheet_name in enumerate(sheet_names, start=1):
                content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)
                try:
                    active_sheet = book.sheet_by_name(sheet_name)
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
                            blank_col_count = 0  # the limit is on consecutive blanks
                            if isinstance(item, float) and str(item)[-2:] == ".0":
                                item = str(item)[:-2]
                            line += str(item) + " "
                            row_has_data = True

                        text_read = text_read or row_has_data
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
                except OSError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    sheets_unread += 1
                    first_unread = first_unread or f"sheet {sheet_number:,}: {type(exc).__name__}"
                final = content_buffer.finalize_content()
                if final:
                    yield final
            if sheets_unread:
                source.report_unread(
                    f"{sheets_unread:,} of {len(sheet_names):,} Excel sheets could not be read, first {first_unread}",
                    nothing_read=sheets_unread == len(sheet_names) and not text_read,
                )
        finally:
            book.release_resources()


handler = XlsHandler()
