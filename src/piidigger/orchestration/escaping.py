"""One way to make text writable to a UTF-8 file, shared by the log and the results files.

A POSIX file name that is not valid UTF-8 reaches Python as surrogate escapes,
which strict UTF-8 cannot encode.  Escaping them the same way everywhere keeps
a name looking the same in the log as in the results files.

Each surrogate escape is written as the byte it stands for, so a name holding
the byte 0xFF reads bad\\xff.txt: the name a user can find on disk.  On Windows,
where a file name is UTF-16, a lone surrogate is part of the name itself and is
written as \\uXXXX.
"""

from __future__ import annotations

import codecs
import sys

from piidigger.models.results import ResultRecord

# The codec error handler for any file that must take every string: pass it as
# errors= to open() or str.encode().
UTF8_ERRORS = "piidigger.escape"

# Python marks an undecodable byte in a file name as U+DC80..U+DCFF only where
# the file system encoding uses surrogateescape, i.e. on POSIX.
_SURROGATE_BYTES = sys.getfilesystemencodeerrors() == "surrogateescape"


def _escape(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeEncodeError):
        raise error
    escaped = []
    for char in error.object[error.start : error.end]:
        code = ord(char)
        if _SURROGATE_BYTES and 0xDC80 <= code <= 0xDCFF:
            escaped.append(f"\\x{code - 0xDC00:02x}")
        else:
            escaped.append(f"\\u{code:04x}")
    return "".join(escaped), error.end


codecs.register_error(UTF8_ERRORS, _escape)


def utf8_escaped(text: str) -> str:
    """text with anything UTF-8 cannot encode, such as a surrogate escape, as a backslash escape."""
    return text.encode("utf-8", UTF8_ERRORS).decode("utf-8")


def _encodable(text: str | None) -> bool:
    if text is None or text.isascii():
        return True
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def escaped_record(record: ResultRecord) -> ResultRecord:
    """record with every string escaped as utf8_escaped() does.

    Nearly every record needs no escaping, and the JSON sink calls this for
    each one, so such a record is returned as is rather than copied.
    """
    member = record.source_member_path
    container = record.source_container_type
    strings = [record.source_path, member, container, record.handler]
    for match_type, values in record.matches.items():
        strings.append(match_type)
        strings.extend(values)
    if all(_encodable(text) for text in strings):
        return record
    return record.model_copy(
        update={
            "source_path": utf8_escaped(record.source_path),
            "source_member_path": None if member is None else utf8_escaped(member),
            "source_container_type": None if container is None else utf8_escaped(container),
            "handler": utf8_escaped(record.handler),
            "matches": {
                utf8_escaped(match_type): [utf8_escaped(value) for value in values]
                for match_type, values in record.matches.items()
            },
        }
    )
