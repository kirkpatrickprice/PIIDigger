from __future__ import annotations

from pathlib import Path
from zipfile import BadZipFile, ZipFile

from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "zip"


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
