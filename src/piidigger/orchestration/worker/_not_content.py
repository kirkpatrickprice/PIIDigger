"""Files that the system or an application writes beside the user's files, and that hold none of their content.

Every file the scan would open is checked first: a file on disk in its scan
task, an archive on disk before it is listed, and an archive member once it is
extracted.  The folder listing goes by names only, so it opens nothing.  A match
is skipped unread and counted under "Skipped by design".  `piidigger inspect
file` checks at the same point, so it never disagrees with a scan.  Each check
needs the file's name and the start of its content, read only when the name
matches.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from piidigger.orchestration.counters import FILES_MACOS_METADATA, FILES_OFFICE_LOCK

# The largest Office lock file the check accepts.  Real ones are about 162 to
# 165 bytes.  One byte more is read, to tell a larger file apart.
_LOCK_FILE_MAX_BYTES: int = 1024

# The lowest byte a printable character can be: anything below is a control
# character, such as the newline in a short HTML or XML file.
_FIRST_PRINTABLE: int = 0x20
_DEL: int = 0x7F

# The name's length byte is at most this.  In the published descriptions of
# the format, the name sits in a field of 54 bytes, length byte included.  The
# bound also means a text file that starts with a letter, "<" or "{" never has
# its first byte taken for a length.
_LOCK_NAME_MAX: int = 53

# After the name, a lock file pads its field with spaces or NULs.  Text has a
# letter or digit there as often as not.
_LOCK_PADDING: bytes = b" \x00"

# AppleDouble (RFC 1740): macOS writes "._<name>" beside a file to hold its
# Finder metadata and resource fork on a volume that cannot store them, such as
# FAT, exFAT or a network share.  Its first four bytes are this magic number.
APPLEDOUBLE_PREFIX: str = "._"
# Finder's zips keep AppleDouble files in a top-level folder of this name.
_MACOS_FOLDER: str = "__MACOSX"
_APPLEDOUBLE_MAGIC: bytes = b"\x00\x05\x16\x07"

# Office lock ("owner") file: Word, Excel and PowerPoint write "~$<name>" beside
# a document while it is open.  It holds only the name of the person who opened
# it, and starts with that name's length in one byte, then the name.
_OFFICE_LOCK_PREFIX: str = "~$"
_OFFICE_EXTS: frozenset[str] = frozenset(
    {
        # Word
        ".doc",
        ".docm",
        ".docx",
        ".dot",
        ".dotm",
        ".dotx",
        # Excel
        ".xls",
        ".xlsb",
        ".xlsm",
        ".xlsx",
        ".xlt",
        ".xltm",
        ".xltx",
        # PowerPoint
        ".pot",
        ".potm",
        ".potx",
        ".pps",
        ".ppsm",
        ".ppsx",
        ".ppt",
        ".pptm",
        ".pptx",
    }
)

APPLEDOUBLE: str = "macOS metadata (an AppleDouble file)"
OFFICE_LOCK_FILE: str = "an Office lock file"

# The counter each reason adds to.
_COUNTERS: dict[str, str] = {APPLEDOUBLE: FILES_MACOS_METADATA, OFFICE_LOCK_FILE: FILES_OFFICE_LOCK}


def not_content_counters(reason: str, size: int | None) -> dict[str, int]:
    """The counters for one file skipped for reason, with its size when the bytes progress bar counted it.

    An archive on disk passes None: its own size never counts toward bytes
    found, only its members' sizes do.
    """
    counters = {_COUNTERS[reason]: 1}
    if size is not None:
        counters["bytes_not_scanned"] = size
    return counters


def comparable_name(name: str) -> str:
    """A member name in the form macOS metadata and its original are compared in.

    "tar -C dir ." stores "./a" and some tools store "a", and Windows tools may
    use backslashes.
    """
    return name.replace("\\", "/").removeprefix("./")


def appledouble_original(name: str) -> str | None:
    """The name of the file an AppleDouble file of this name would describe, or None if the name is not one.

    name is a file name, or an archive member's comparable_name().  macOS names
    the metadata for "dir/name" "dir/._name", and Finder's zips move it under a
    top-level "__MACOSX/" folder.  Only the name is looked at, so a match is a
    shape, not proof: the caller decides what the shape is allowed to settle.
    """
    folder, _, last = name.rpartition("/")
    if not last.startswith(APPLEDOUBLE_PREFIX) or last == APPLEDOUBLE_PREFIX:
        return None
    parts = folder.split("/") if folder else []
    if parts and parts[0] == _MACOS_FOLDER:
        parts = parts[1:]
    return "/".join([*parts, last.removeprefix(APPLEDOUBLE_PREFIX)])


def in_macos_folder(name: str) -> bool:
    """Return True for an archive member's comparable_name() inside Finder's top-level "__MACOSX/" folder."""
    return name.startswith(_MACOS_FOLDER + "/")


def original_beside(name: str, sibling_names: Collection[str]) -> str | None:
    """The original a "._name" file on disk describes, when that original is in the same folder.

    sibling_names is the folder's listing, so the folder listing and `inspect
    file` decide the same way: by exact name, as macOS writes it.
    """
    original = appledouble_original(name)
    return original if original is not None and original in sibling_names else None


def member_basename(name: str) -> str:
    """The last part of an archive member's name, which the checks go by."""
    return comparable_name(name).rsplit("/", 1)[-1]


def is_appledouble(name: str, content: bytes) -> bool:
    """Return True for an AppleDouble file: its name starts with "._" and its content with the RFC 1740 magic."""
    return name.startswith(APPLEDOUBLE_PREFIX) and content[:4] == _APPLEDOUBLE_MAGIC


def is_office_lock_file(name: str, content: bytes) -> bool:
    """Return True for an Office lock file: a "~$" name with an Office extension, and a lock file's shape.

    content is the start of the file, up to one byte past _LOCK_FILE_MAX_BYTES.
    The shape is the name of the person who opened the document, as Office
    writes it:
      - a file of at most _LOCK_FILE_MAX_BYTES
      - a first byte from 1 to _LOCK_NAME_MAX, the name's length
      - that many printable characters
      - then a space or NUL, the start of the field's padding

    A real document saved under a "~$" name fails it, so it is still scanned:
      - A zip or OLE file starts with a binary byte, or one larger than the name
        field allows.
      - HTML, XML and RTF (which .xls and .doc files often are) start with "<"
        or "{", both above _LOCK_NAME_MAX, and so does text that starts with a
        letter.
      - Text that starts with a digit or punctuation has to have exactly a
        space or NUL at the right place after it, too.

    The shape errs toward reading.  A lock file of some Office version that
    does not match is read, and holds nothing to find.
    """
    if not name.startswith(_OFFICE_LOCK_PREFIX) or Path(name).suffix.lower() not in _OFFICE_EXTS:
        return False
    if not content or len(content) > _LOCK_FILE_MAX_BYTES:
        return False
    length = content[0]
    if not 0 < length <= _LOCK_NAME_MAX or len(content) <= 1 + length:
        return False
    owner = content[1 : 1 + length]
    padded = content[1 + length] in _LOCK_PADDING
    return padded and min(owner) >= _FIRST_PRINTABLE and _DEL not in owner


def not_content_reason(path: Path, name: str | None = None) -> str | None:
    """Say why the file at path is not the user's content, or return None if it may be.

    name is the name the checks go by, path.name by default.  For an archive
    member it is the member's own name, since the extracted file has a generated
    one.

    A file that cannot be read returns None, so the scan reads it and reports
    the error itself.
    """
    if name is None:
        name = path.name
    if not name.startswith((APPLEDOUBLE_PREFIX, _OFFICE_LOCK_PREFIX)):
        return None
    try:
        with path.open("rb") as f:
            # The byte past the largest lock file is what tells a larger file
            # apart; is_office_lock_file() relies on it, so never read less.
            content = f.read(_LOCK_FILE_MAX_BYTES + 1)
    except OSError:
        return None
    if is_appledouble(name, content):
        return APPLEDOUBLE
    if is_office_lock_file(name, content):
        return OFFICE_LOCK_FILE
    return None
