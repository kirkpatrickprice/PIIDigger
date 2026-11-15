from __future__ import annotations

import gzip
import lzma
import shutil
import tarfile
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, cast

from piidigger._progress_io import open_with_progress
from piidigger.archivehandlers._dest_names import MemberDestNames, discard_partial
from piidigger.exceptions import ArchivePartlyReadError, ArchiveReadError
from piidigger.models.archive import MemberInfo

# A truncated compressed tar raises EOFError.  A corrupt .tar.xz raises LZMAError,
# a corrupt .tar.gz can raise zlib.error, and a corrupt zstd tar raises ZstdError,
# none of them an OSError.
_READ_ERRORS: tuple[type[Exception], ...] = (tarfile.TarError, OSError, EOFError, lzma.LZMAError, zlib.error)
try:
    from compression.zstd import ZstdError
except ImportError:
    # zstd is optional when CPython is built, and tarfile treats it that way too:
    # without it, a zstd tar does not open, and everything else still works.
    pass
else:
    _READ_ERRORS = (*_READ_ERRORS, ZstdError)

_DRAIN_CHUNK_BYTES = 1024 * 1024

ARCHIVE_TYPE = "tar"
HANDLES = {
    "ext": [".tar", ".tgz", ".tbz2", ".tbz", ".txz", ".tar.gz", ".tar.bz2", ".tar.xz"],
}


def _read_to_end(read: Callable[[int], bytes]) -> None:
    """Read a compressed stream to its end, which makes its reader verify the checksum.

    tarfile takes a damaged header part way through for the end of the archive,
    with no error, so damage to a compressed tar can otherwise look like a
    complete listing that is merely short.  A tar that ended normally has only
    its padding left to read.  An uncompressed tar has no checksum to verify.

    Bytes appended after a complete gzip stream are not damage: the stream has
    passed its checksum by then, and gzip itself only warns about them.
    """
    try:
        while read(_DRAIN_CHUNK_BYTES):
            pass
    except gzip.BadGzipFile as exc:
        if not str(exc).startswith("Not a gzipped file"):
            raise


class TarArchiveHandler:
    def list_members(self, archive_path: Path, on_progress: Callable[[], None] | None = None) -> list[MemberInfo]:
        """List members in archive order, one header at a time.

        Raises ArchivePartlyReadError, holding the members already listed, when
        the archive fails part way through; ArchiveReadError when nothing could
        be listed.
        """
        members: list[MemberInfo] = []
        try:
            with open_with_progress(archive_path, on_progress) as raw, tarfile.open(fileobj=raw, mode="r:*") as tf:
                # An uncompressed tar is read straight from the file, which
                # seeks past member data for free, so nothing has to be
                # decompressed to reach a member.  A compressed one is read
                # through a gzip/bzip2/xz reader that wraps the file.
                compressed = tf.fileobj is not raw
                while (info := tf.next()) is not None:
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
                            decompress_offset=info.offset if compressed else 0,
                        )
                    )
                if compressed:
                    _read_to_end(tf.fileobj.read)
                return members
        except _READ_ERRORS as exc:
            if members:
                raise ArchivePartlyReadError(
                    f"archive is damaged; scanning the {len(members):,} member(s) listed: {exc}", members
                ) from exc
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
        on_discard: Callable[[str, Path], None] | None = None,
    ) -> None:
        """Walk the tar stream forward once, extracting the requested members as they go by.

        next() reads one header at a time, so the part of the archive before the
        first requested member is decompressed once and never rewound, and the
        walk stops after the last requested member instead of reading to the
        end.  getmember() would do neither: it reads every header to the end of
        the archive, and the extract that follows seeks back, which makes a
        gzip/bzip2/xz reader start again from byte 0.

        A path that occurs more than once is extracted at its first occurrence
        as a regular file.

        Each member's data is copied from extractfile() to a generated name in
        dest_dir (see MemberDestNames).  tarfile's own extract() is not used:
        it writes to the member's name, and the name is what can go wrong on
        Windows.  Only regular files are extracted, so there are no links or
        device nodes for its data filter to refuse, and enumeration has
        already rejected names that climb out of the archive.

        A member whose file the OS refuses to write goes to on_failed, and the
        walk carries on with the next member.
        """
        wanted = set(member_paths)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with open_with_progress(archive_path, on_progress) as raw, tarfile.open(fileobj=raw, mode="r:*") as tf:
                names = MemberDestNames(dest_dir)
                while wanted:
                    info = tf.next()
                    if info is None:
                        break
                    # A link or folder of the same name is skipped, as in
                    # list_members(), so the member found is the one listed.
                    if info.name not in wanted or not info.isfile():
                        continue
                    wanted.discard(info.name)
                    if on_started is not None:
                        on_started(info.name)
                    dest = names.next_path(info.name)
                    try:
                        # extractfile() returns None only for a non-file, skipped above.
                        src = cast("IO[bytes]", tf.extractfile(info))
                        with src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out)
                    except _READ_ERRORS as exc:
                        discard_partial(info.name, dest, on_discard)
                        if not isinstance(exc, OSError):
                            # A broken stream (EOFError, LZMAError, zlib.error,
                            # ReadError) ends the walk: nothing after it can be
                            # read.  The partly written file is handed over first.
                            raise

                        # An OSError, including a corrupt gzip stream
                        # (BadGzipFile), fails this member; the next next()
                        # then hits the same broken stream and ends the walk
                        # with ArchiveReadError.
                        if on_failed is not None:
                            on_failed(info.name, str(exc))
                        continue
                    on_extracted(info.name, dest)
        except _READ_ERRORS as exc:
            raise ArchiveReadError(str(exc)) from exc


handler = TarArchiveHandler()
