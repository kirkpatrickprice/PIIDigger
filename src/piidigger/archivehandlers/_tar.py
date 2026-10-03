from __future__ import annotations

import tarfile
from collections.abc import Callable, Sequence
from pathlib import Path

from piidigger.archivehandlers._progress_io import open_with_progress
from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

ARCHIVE_TYPE = "tar"
HANDLES = {
    "ext": [".tar", ".tgz", ".tbz2", ".tbz", ".txz", ".tar.gz", ".tar.bz2", ".tar.xz"],
}


class TarArchiveHandler:
    def list_members(self, archive_path: Path, on_progress: Callable[[], None] | None = None) -> list[MemberInfo]:
        try:
            with open_with_progress(archive_path, on_progress) as raw, tarfile.open(fileobj=raw, mode="r:*") as tf:
                # An uncompressed tar is read straight from the file, which
                # seeks past member data for free, so nothing has to be
                # decompressed to reach a member.  A compressed one is read
                # through a gzip/bzip2/xz reader that wraps the file.
                compressed = tf.fileobj is not raw
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
                            decompress_offset=info.offset_data if compressed else 0,
                        )
                    )
                return members
        except (tarfile.TarError, OSError) as exc:
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
        """Walk the tar stream forward once, extracting the requested members as they go by.

        next() reads one header at a time, so the part of the archive before the
        first requested member is decompressed once and never rewound, and the
        walk stops after the last requested member instead of reading to the
        end.  getmember() would do neither: it reads every header to the end of
        the archive, and the extract that follows seeks back, which makes a
        gzip/bzip2/xz reader start again from byte 0.

        A path that occurs more than once is extracted at its first occurrence.

        The extracted path comes from the member as the data filter rewrites
        it, never from the raw name.  The filter strips a leading separator,
        and on Windows that includes a backslash.  So dest_dir / "\\x\\y.txt"
        would name a file outside dest_dir — on the drive root — while the
        filter actually wrote dest_dir\\x\\y.txt.  The caller deletes the path
        it is given, so getting this wrong would delete a host file.

        A member that cannot be written on its own (the filter rejects it, or
        the OS refuses the name or path) goes to on_failed, and the walk
        carries on with the next member.
        """
        wanted = set(member_paths)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            root = dest_dir.resolve()
            with open_with_progress(archive_path, on_progress) as raw, tarfile.open(fileobj=raw, mode="r:*") as tf:
                while wanted:
                    info = tf.next()
                    if info is None:
                        break
                    if info.name not in wanted:
                        continue
                    wanted.discard(info.name)
                    if on_started is not None:
                        on_started(info.name)
                    try:
                        safe = tarfile.data_filter(info, str(dest_dir))
                        tf.extract(safe, path=dest_dir, filter="data")
                    except (tarfile.FilterError, OSError) as exc:
                        # Includes a corrupt stream (gzip raises BadGzipFile,
                        # an OSError).  That fails this member; the next
                        # next() then hits the same broken stream and ends
                        # the walk with ArchiveReadError.
                        if on_failed is not None:
                            on_failed(info.name, str(exc))
                        continue
                    extracted = dest_dir / safe.name
                    if not extracted.resolve().is_relative_to(root) or not extracted.is_file():
                        if on_failed is not None:
                            on_failed(info.name, "member not found in the extraction folder")
                        continue
                    on_extracted(info.name, extracted)
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise ArchiveReadError(str(exc)) from exc


handler = TarArchiveHandler()
