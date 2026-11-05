from __future__ import annotations

import shutil
import stat
import struct
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO
from zipfile import BadZipFile, ZipFile, ZipInfo

from piidigger._progress_io import open_with_progress
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

# Compression method 9, Deflate64.  Windows Explorer writes it for large files,
# and zipfile cannot read it (it has no constant for it either), so these
# members are decoded with inflate64 by _copy_deflate64().
_DEFLATE64 = 9

# The local file header: a fixed 30-byte part, then the name and extra field.
# Their lengths are the two unsigned shorts at offset 26.
_LOCAL_HEADER_SIZE = 30
_LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"
_COPY_CHUNK = 64 * 1024


def _copy_deflate64(raw: IO[bytes], info: ZipInfo, out: IO[bytes]) -> None:
    """Decode one Deflate64 member from the archive stream into out.

    Reads only this member's bytes: from its local header at header_offset,
    compress_size bytes of data.  Zip compresses every member on its own, so
    random access is kept.  The sizes and CRC come from the central directory,
    which zipfile has already parsed, ZIP64 fields included.

    Raises BadZipFile for a bad header, corrupt or truncated data, or a size
    or CRC mismatch, so one bad member spoils only itself.
    """
    import inflate64  # lazy: only loaded when a Deflate64 member is read

    if info.flag_bits & 0x1:
        raise NotImplementedError("encrypted Deflate64 member")
    raw.seek(info.header_offset)
    header = raw.read(_LOCAL_HEADER_SIZE)
    if len(header) != _LOCAL_HEADER_SIZE or not header.startswith(_LOCAL_HEADER_SIGNATURE):
        raise BadZipFile(f"bad local file header for {info.filename!r}")
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    raw.seek(name_len + extra_len, 1)

    inflater = inflate64.Inflater()
    remaining = info.compress_size
    size = 0
    crc = 0
    while remaining:
        chunk = raw.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise BadZipFile(f"truncated Deflate64 data for {info.filename!r}")
        remaining -= len(chunk)
        try:
            data = inflater.inflate(chunk)
        except ValueError as exc:  # inflate64 reports corrupt data as ValueError
            raise BadZipFile(f"corrupt Deflate64 data for {info.filename!r}: {exc}") from exc
        out.write(data)
        size += len(data)
        crc = zlib.crc32(data, crc)
    if not inflater.eof:
        raise BadZipFile(f"truncated Deflate64 data for {info.filename!r}")
    if size != info.file_size:
        raise BadZipFile(f"size mismatch for {info.filename!r}: {size} != {info.file_size}")
    if crc != info.CRC:
        raise BadZipFile(f"Bad CRC-32 for file {info.filename!r}")


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
                        if entry.compress_type == _DEFLATE64:
                            # Safe alongside zf: each zf.open() seeks to its own offset.
                            with dest.open("wb") as out:
                                _copy_deflate64(raw, entry, out)
                        else:
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
