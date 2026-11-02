"""One way to make text writable to a UTF-8 file, shared by the log and the results files.

A POSIX file name that is not valid UTF-8 reaches Python as surrogate escapes,
which strict UTF-8 cannot encode.  Escaping them the same way everywhere keeps
a name looking the same in the log as in the results files.
"""

from __future__ import annotations


def utf8_escaped(text: str) -> str:
    """text with anything UTF-8 cannot encode, such as a surrogate escape, as a backslash escape."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")
