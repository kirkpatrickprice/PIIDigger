from __future__ import annotations

from pathlib import Path
from typing import Any

from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "7z"
HANDLES = {
    "ext": [".7z"],
}


def _is_symlink(info: Any) -> bool:
    """Return True if info is a symbolic link entry, not real file content."""
    return bool(info.is_symlink)


class SevenZArchiveHandler:
    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        try:
            import py7zr  # lazy: only loaded when a .7z file is encountered
            with py7zr.SevenZipFile(archive_path, mode="r") as szf:
                all_encrypted = szf.needs_password()
                raw = szf.list()
            return [
                MemberInfo(
                    name=info.filename,
                    uncompressed_size=info.uncompressed or 0,
                    compressed_size=info.compressed or 0,
                    is_dir=info.is_directory,
                    is_encrypted=all_encrypted,
                )
                for info in raw
                if not _is_symlink(info)
            ]
        except ImportError as exc:
            raise ArchiveReadError(f"py7zr is required for .7z archives: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — py7zr exception hierarchy varies by version
            raise ArchiveReadError(str(exc)) from exc

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        try:
            import py7zr

            dest_dir.mkdir(parents=True, exist_ok=True)
            with py7zr.SevenZipFile(archive_path, mode="r") as szf:
                szf.extract(path=str(dest_dir), targets=[member_path])

            extracted = dest_dir / member_path
            if not extracted.exists():
                raise ArchiveReadError(
                    f"member {member_path!r} not found after extraction from {archive_path}"
                )
            return extracted
        except ArchiveReadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArchiveReadError(str(exc)) from exc


handler = SevenZArchiveHandler()
