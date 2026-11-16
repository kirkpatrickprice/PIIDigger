"""One way to write a file name anywhere it leaves the process: log, results files and terminal.

A POSIX file name that is not valid UTF-8 reaches Python as surrogate escapes,
which strict UTF-8 cannot encode.  Escaping them the same way everywhere keeps
a name looking the same on screen, in the log and in the results files.  A name
can also hold control characters, which the log and the terminal escape too.

Each surrogate escape is written as the byte it stands for, so a name holding
the byte 0xFF reads bad\\xff.txt: the name a user can find on disk.  On Windows,
where a file name is UTF-16, a lone surrogate is part of the name itself and is
written as \\uXXXX.
"""

from __future__ import annotations

import codecs
import io
import sys
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only escaped_record() needs it; this module stays free of the models package at run time
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


def _escape_char(char: str) -> str:
    """A character as an escape: \\xNN, \\uNNNN or \\UNNNNNNNN.

    As repr() writes it, except that \\xNN is used below 0x80 only.  A byte
    that is not UTF-8 is always 0x80 or above, and the log writes it as \\xNN,
    so from 0x80 on a character gets \\uNNNN where repr() would write \\xNN.
    """
    code = ord(char)
    if code < 0x80:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def escape_unprintable(text: str) -> str:
    """text with each character repr() would escape written as an escape.

    A file name can hold any character.  Written raw, a carriage return or an
    escape sequence rewrites what a terminal shows, U+2028 splits the line for
    str.splitlines(), and U+202E reverses the rest of it on screen.  Tab stays,
    and so do surrogates: the encoder writes those as \\xNN (UTF8_ERRORS), the
    byte of a POSIX name that is not UTF-8.  So does a space such as U+00A0 or
    U+202F, which macOS puts in screenshot names: it moves nothing, and written
    raw the name still matches what a file manager shows.
    """
    if text.isprintable():
        return text
    return "".join(char if _kept(char) else _escape_char(char) for char in text)


def _kept(char: str) -> bool:
    return (
        char.isprintable()
        or char == "\t"
        or "\ud800" <= char <= "\udfff"
        or unicodedata.category(char) == "Zs"  # space separators; line and paragraph separators are Zl and Zp
    )


def terminal_text(text: str) -> str:
    """text made safe to draw on a terminal: no control characters, nothing it cannot encode.

    A file name can rewrite the screen with an escape sequence or break a row
    with a newline, and one that is not valid UTF-8 cannot be encoded at all.
    """
    return utf8_escaped(escape_unprintable(text))


def escape_std_streams() -> None:
    """Make stdout and stderr escape what they cannot encode instead of raising.

    A file name that is not valid UTF-8 reaches every message that names it: a
    CLI error, an inspect result, an exception's text.  Escaping at the stream
    covers each of them, and the next one written, the way the log file does.
    Only a real TextIOWrapper can be reconfigured; a test's stand-in is left as is.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors=UTF8_ERRORS)


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
