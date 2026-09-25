from __future__ import annotations

import stat
from pathlib import Path
from zipfile import BadZipFile, ZipFile, ZipInfo

from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "zip"
HANDLES = {
    "ext": [".zip"],
}

# ZipInfo.create_system value meaning "this entry's metadata was written by a
# Unix zip tool" — only then are external_attr's upper 16 bits a unix st_mode.
_UNIX_CREATE_SYSTEM = 3


def _is_symlink(info: ZipInfo) -> bool:
    """Return True if info is a Unix symlink entry, not real file content.

    Windows-authored zips never set create_system == 3, so this is always
    False for them regardless of external_attr's contents.
    """
    if info.create_system != _UNIX_CREATE_SYSTEM:
        return False
    return stat.S_ISLNK(info.external_attr >> 16)


class ZipArchiveHandler:
    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        try:
            with ZipFile(archive_path, "r") as zf:
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

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / Path(member_path).name
            with ZipFile(archive_path, "r") as zf:
                dest.write_bytes(zf.read(member_path))
            return dest
        except (BadZipFile, OSError, KeyError) as exc:
            raise ArchiveReadError(str(exc)) from exc


handler = ZipArchiveHandler()
