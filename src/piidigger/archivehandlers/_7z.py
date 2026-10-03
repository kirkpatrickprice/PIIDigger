from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from piidigger.archivehandlers._progress_io import open_with_progress
from piidigger.exceptions import ArchiveReadError
from piidigger.models.archive import MemberInfo

if TYPE_CHECKING:
    from py7zr.io import Py7zIO

ARCHIVE_TYPE = "7z"
HANDLES = {
    "ext": [".7z"],
}


def _is_symlink(info: Any) -> bool:
    """Return True if info is a symbolic link entry, not real file content."""
    return bool(info.is_symlink)


class SevenZArchiveHandler:
    def list_members(self, archive_path: Path, on_progress: Callable[[], None] | None = None) -> list[MemberInfo]:
        """List members in stored order, with each member's offset inside its folder.

        Reads py7zr's own file list (SevenZipFile.files) rather than list(),
        because only the file list says which folder each member belongs to.  A
        solid folder is one compressed stream, so a member's decompress_offset
        is the uncompressed size of the members stored before it in that folder.
        """
        try:
            import py7zr  # lazy: only loaded when a .7z file is encountered

            with open_with_progress(archive_path, on_progress) as raw, py7zr.SevenZipFile(raw, mode="r") as szf:
                all_encrypted = szf.needs_password()
                members = []
                folder_offsets: dict[int, int] = {}
                for info in szf.files:
                    size = info.uncompressed or 0
                    folder = info.folder
                    offset = folder_offsets.get(id(folder), 0) if folder is not None else 0
                    if folder is not None:
                        folder_offsets[id(folder)] = offset + size
                    if _is_symlink(info):
                        continue
                    members.append(
                        MemberInfo(
                            name=info.filename,
                            uncompressed_size=size,
                            compressed_size=info.compressed or 0,
                            is_dir=info.is_directory,
                            is_encrypted=all_encrypted,
                            decompress_offset=offset,
                        )
                    )
                return members
        except ImportError as exc:
            raise ArchiveReadError(f"py7zr is required for .7z archives: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — py7zr exception hierarchy varies by version
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
        """Extract the requested members in one pass, handing each to on_extracted as it completes.

        py7zr decompresses each solid folder from its start, so one call for the
        whole run reaches every member with one pass over the folder, and py7zr
        stops after the last requested member in it.

        py7zr writes members through a WriterFactory.  _ScanWriterFactory writes
        each one to a file in dest_dir and calls on_extracted from the writer's
        close(), which py7zr calls only once the member's CRC has been checked
        and before it starts on the next member.

        The archive is passed to py7zr as an open file rather than a path.  That
        keeps py7zr on one thread, so members finish strictly one at a time, in
        stored order.

        A member whose file the OS refuses to create (a reserved name such as
        aux.txt on Windows, a path too long) goes to on_failed; its data is
        decompressed and discarded so the run carries on.  A CRC or
        decompression error ends the whole run instead: in a solid folder,
        nothing after a corrupt member can be decoded.
        """
        try:
            import py7zr

            dest_dir.mkdir(parents=True, exist_ok=True)
            factory = _ScanWriterFactory(dest_dir, member_paths, on_started, on_extracted, on_failed)
            try:
                with open_with_progress(archive_path, on_progress) as raw, py7zr.SevenZipFile(raw, mode="r") as szf:
                    szf.extract(path=dest_dir, targets=list(member_paths), factory=factory)
            finally:
                factory.close_abandoned()
        except ImportError as exc:
            raise ArchiveReadError(f"py7zr is required for .7z archives: {exc}") from exc
        except ArchiveReadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArchiveReadError(str(exc)) from exc


class _ScanWriterFactory:
    """py7zr WriterFactory that writes each member to disk and reports it when complete.

    py7zr calls create() with the output path it computed for a member: dest_dir
    joined with the member name and normalised without touching the disk.  The
    same join, done here, maps that path back to the member name.

    Defined without subclassing py7zr's WriterFactory so this module does not
    import py7zr until a .7z file is actually read; py7zr only calls create().
    """

    def __init__(
        self,
        dest_dir: Path,
        member_paths: Sequence[str],
        on_started: Callable[[str], None] | None,
        on_extracted: Callable[[str, Path], None],
        on_failed: Callable[[str, str], None] | None,
    ) -> None:
        self._members = {(dest_dir / member).as_posix(): member for member in member_paths}
        self._on_started = on_started
        self._on_extracted = on_extracted
        self._on_failed = on_failed
        self._open: list[_ScanWriter] = []

    def create(self, filename: str) -> Py7zIO:
        from py7zr.io import NullIO

        member = self._members.get(filename)
        if member is None:
            # Not one of ours.  py7zr renames a repeated path to "<name>_<n>";
            # enumeration keeps only the first, so a renamed copy is discarded.
            return NullIO()  # type: ignore[no-untyped-call]  # py7zr leaves NullIO.__init__ unannotated
        if self._on_started is not None:
            self._on_started(member)
        try:
            writer = _ScanWriter(Path(filename), member, self._on_extracted)
        except OSError as exc:
            if self._on_failed is not None:
                self._on_failed(member, str(exc))
            return NullIO()  # type: ignore[no-untyped-call]
        self._open.append(writer)
        return cast("Py7zIO", writer)

    def close_abandoned(self) -> None:
        """Close any file a failed extraction left open, without reporting it.

        py7zr skips close() when a member fails its CRC check, which would leave
        the file handle open and the file undeletable on Windows.
        """
        for writer in self._open:
            writer.discard()
        self._open.clear()


class _ScanWriter:
    """Writes one member to disk; on close(), hands the finished file to on_extracted.

    Duck-types py7zr's Py7zIO (write, read, seek, flush, size, close) rather
    than subclassing it, for the same lazy-import reason as the factory.
    """

    def __init__(self, path: Path, member: str, on_extracted: Callable[[str, Path], None]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._member = member
        self._on_extracted = on_extracted
        self._file: BinaryIO = path.open("w+b")
        self._done = False

    def write(self, s: bytes | bytearray) -> int:
        return self._file.write(s)

    def read(self, size: int | None = None) -> bytes:
        return self._file.read(-1 if size is None else size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def flush(self) -> None:
        self._file.flush()

    def size(self) -> int:
        position = self._file.tell()
        end = self._file.seek(0, 2)
        self._file.seek(position)
        return end

    def close(self) -> None:
        if self._done:
            return
        self._done = True
        self._file.close()
        self._on_extracted(self._member, self._path)

    def discard(self) -> None:
        if not self._done:
            self._done = True
            self._file.close()


handler = SevenZArchiveHandler()
