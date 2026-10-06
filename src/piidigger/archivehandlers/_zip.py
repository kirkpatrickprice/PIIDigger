from __future__ import annotations

import shutil
import stat
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from zipfile import BadZipFile, ZipFile, ZipInfo

from piidigger.archivehandlers._progress_io import open_with_progress
from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "zip"
HANDLES = {
    "ext": [".zip"],
}

# ZipInfo.create_system value meaning "this entry's metadata was written by a
# Unix zip tool" — only then are external_attr's upper 16 bits a unix st_mode.
_UNIX_CREATE_SYSTEM = 3

# Errors that spoil one member but leave the rest of the archive readable: a
# CRC mismatch or corrupt deflate stream, an unsupported compression method,
# an encrypted entry, or an OS refusal to write this member's file (a reserved
# name such as aux.txt on Windows, a path too long, a full disk).
_MEMBER_ERRORS = (BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError, OSError)


def _is_symlink(info: ZipInfo) -> bool:
    """Return True if info is a Unix symlink entry, not real file content.

    Windows-authored zips never set create_system == 3, so this is always
    False for them regardless of external_attr's contents.
    """
    if info.create_system != _UNIX_CREATE_SYSTEM:
        return False
    return stat.S_ISLNK(info.external_attr >> 16)


class ZipArchiveHandler:
    def list_members(self, archive_path: Path, on_progress: Callable[[], None] | None = None) -> list[MemberInfo]:
        try:
            with open_with_progress(archive_path, on_progress) as raw, ZipFile(raw, "r") as zf:
                return [
                    MemberInfo(
                        name=info.filename,
                        uncompressed_size=info.file_size,
                        compressed_size=info.compress_size,
                        is_dir=info.filename.endswith("/"),
                        is_encrypted=bool(info.flag_bits & 0x1),
                    )
                    for info in zf.infolist()
                    if not _is_symlink(info)
                ]
        except (BadZipFile, OSError) as exc:
            raise ArchiveReadError(str(exc)) from exc

    def extract_members(
        self,
        archive_path: Path,
        member_paths: Sequence[str],
        dest_dir: Path,
        *,
        on_extracted: Callable[[str, Path], None],
        on_started: Callable[[str], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        """Open the archive once and stream each requested member to its own file.

        Zip reads any member directly, so the order costs nothing; members are
        taken in the order given.  Each one is extracted flat, to
        dest_dir / basename, which is safe because the caller deletes it before
        the next one is written.

        When a name occurs more than once, the first entry is extracted.  That
        is the entry enumeration ran its size, ratio and encryption checks on;
        taking any other would let an unchecked entry through.
        """
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with open_with_progress(archive_path, on_progress) as raw, ZipFile(raw, "r") as zf:
                first_entry: dict[str, ZipInfo] = {}
                for info in zf.infolist():
                    first_entry.setdefault(info.filename, info)
                for member in member_paths:
                    entry = first_entry.get(member)
                    if entry is None:
                        continue
                    if on_started is not None:
                        on_started(member)
                    dest = dest_dir / Path(member).name
                    # On Windows a name such as "foo/d:run.bat" has the basename
                    # "d:run.bat", and joining a drive-relative name discards
                    # dest_dir.  On POSIX the parent always matches.
                    if dest.parent != dest_dir:
                        if on_failed is not None:
                            on_failed(member, "member name escapes the extraction folder")
                        continue
                    try:
                        with zf.open(entry) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out)
                    except _MEMBER_ERRORS as exc:
                        if on_failed is not None:
                            on_failed(member, str(exc))
                        continue
                    on_extracted(member, dest)
        except (BadZipFile, OSError) as exc:
            raise ArchiveReadError(str(exc)) from exc


handler = ZipArchiveHandler()
