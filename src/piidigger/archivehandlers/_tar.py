from __future__ import annotations

import tarfile
from pathlib import Path

from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "tar"
HANDLES = {
    "ext": [".tar", ".tgz", ".tbz2", ".tbz", ".txz", ".tar.gz", ".tar.bz2", ".tar.xz"],
}


class TarArchiveHandler:
    def list_members(self, archive_path: Path) -> list[MemberInfo]:
        try:
            with tarfile.open(archive_path, mode="r:*") as tf:
                members = []
                for info in tf.getmembers():
                    if not (info.isfile() or info.isdir()):
                        # symlinks, hardlinks, device/FIFO nodes have no
                        # scannable content; skip rather than flag downstream
                        continue
                    members.append(
                        MemberInfo(
                            name=info.name,
                            uncompressed_size=info.size,
                            compressed_size=0,
                            is_dir=info.isdir(),
                            is_encrypted=False,
                        )
                    )
                return members
        except (tarfile.TarError, OSError) as exc:
            raise ArchiveReadError(str(exc)) from exc

    def extract_member(self, archive_path: Path, member_path: str, dest_dir: Path) -> Path:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path, mode="r:*") as tf:
                member = tf.getmember(member_path)
                tf.extract(member, path=dest_dir, filter="data")
            extracted = dest_dir / member_path
            if not extracted.exists():
                raise ArchiveReadError(
                    f"member {member_path!r} not found after extraction from {archive_path}"
                )
            return extracted
        except ArchiveReadError:
            raise
        except (tarfile.TarError, OSError, KeyError) as exc:
            raise ArchiveReadError(str(exc)) from exc


handler = TarArchiveHandler()
