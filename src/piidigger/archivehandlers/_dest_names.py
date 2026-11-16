from __future__ import annotations

import re
from collections.abc import Callable, Collection
from pathlib import Path

# An extension that is safe in a file name on every platform: ASCII letters,
# digits, "_" and "-" after one dot.
_SAFE_EXT: re.Pattern[str] = re.compile(r"\.[A-Za-z0-9_-]{1,32}")


class MemberDestNames:
    """The files one extract_members() call writes its members to: dest_dir / "<n><ext>".

    A member's own name is never used on disk.  It can hold a character Windows
    does not allow in a name (a colon, say, from a timestamp), name a device
    such as con.txt, or nest deep enough to pass 260 characters under the temp
    folder.  A generated name avoids all three, and it cannot point outside
    dest_dir.  The member's real name still goes to on_extracted, the results
    and the logs.

    n counts up, and a name already in dest_dir is passed over.  The same worker
    can retry a task in the same folder, and a file an antivirus scanner still
    holds there from the last try must not be overwritten.  dest_dir is listed
    once, when the object is made: n only counts up, so a file this call writes
    can never come up again.  Names in avoid are passed over too (see
    _7z._rename_targets()).

    The extension is the one the member's file handler was chosen by, so the
    file on disk still says what it is, to FilesystemItem.ext and to any reader
    that looks at its name.  It is dropped if it is not safe in a name.

    Not thread-safe; each extract_members() call makes its own.
    """

    def __init__(self, dest_dir: Path, avoid: Collection[str] = ()) -> None:
        from piidigger.filehandlers import file_ext  # lazy: filehandlers imports every reader

        self._file_ext = file_ext
        self._dest_dir = dest_dir
        try:
            present = {entry.name for entry in dest_dir.iterdir()}
        except FileNotFoundError:
            present = set()
        self._avoid = frozenset(avoid) | present
        self._next = 0

    def next_path(self, member: str) -> Path:
        ext = self._file_ext(member)
        if not _SAFE_EXT.fullmatch(ext):
            ext = ""
        while True:
            name = f"{self._next}{ext}"
            self._next += 1
            if name not in self._avoid:
                return self._dest_dir / name


def discard_partial(member: str, dest: Path, on_discard: Callable[[str, Path], None] | None) -> None:
    """Hand a failed member's partly written file to on_discard, if the file was created.

    Call it before on_failed, as the ArchiveHandler protocol describes.
    """
    if on_discard is not None and dest.exists():
        on_discard(member, dest)
