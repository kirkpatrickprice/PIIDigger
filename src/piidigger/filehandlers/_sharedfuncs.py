######################################################
############ Shared File Handler Functions ###########
######################################################

import io
from collections import deque


def is_encrypted_office_file(source) -> bool:  # source: ScannableItem
    """Whether source is a Word or Excel document saved with a password.

    Office writes such a .docx or .xlsx as an OLE compound file that holds the
    encrypted zip in an EncryptedPackage stream (MS-OFFCRYPTO), not as a zip.
    Called only after a reader has failed to open the file, so other files
    never pay for the check.  An OSError reaches the caller.

    An encrypted workbook can be hundreds of MB, so only its header, allocation
    table and directory are read, through source.open_stream(), which keeps a
    slow share's task alive.  xlrd holds the allocation table as a list, about
    7% of the file's size.
    """
    from xlrd.compdoc import SIGNATURE  # lazy: xlrd import triggers SyntaxWarning

    data = source.open_bytes()
    if data is not None:
        return data.startswith(SIGNATURE) and _lists_encrypted_package(data)
    with source.open_stream() as stream:
        # Below the buffer: a buffered read refills a whole buffer (128 KiB) for
        # each 512-byte sector, and the allocation table can be spread out.
        raw = getattr(stream, "raw", stream)
        slices = _FileSlices(raw, raw.seek(0, io.SEEK_END))
        if slices[: len(SIGNATURE)] != SIGNATURE:
            return False
        return _lists_encrypted_package(slices)


class _FileSlices:
    """A seekable file read as slices of bytes, which is all xlrd's CompDoc asks of its input.

    Each slice reads only its own bytes, so only what CompDoc looks at is read.
    Unlike a memory map, a failed read is an OSError, not a crash of the worker.
    """

    def __init__(self, stream, size: int) -> None:  # stream: an unbuffered IO[bytes]
        self._stream = stream
        self._size = size

    def __len__(self) -> int:
        return self._size

    def __getitem__(self, key: slice) -> bytes:
        start, stop, _ = key.indices(self._size)
        self._stream.seek(start)
        chunks = []
        wanted = stop - start
        while wanted > 0 and (chunk := self._stream.read(wanted)):  # an unbuffered read can return less
            chunks.append(chunk)
            wanted -= len(chunk)
        return b"".join(chunks)


def _lists_encrypted_package(mem) -> bool:  # mem: bytes or _FileSlices
    """Whether the compound file in mem lists an EncryptedPackage stream in its directory.

    The directory listing, not locate_named_stream(): that one copies a stream
    stored in pieces, which here is the whole encrypted document.
    """
    from xlrd.compdoc import CompDoc  # lazy: xlrd import triggers SyntaxWarning

    try:
        # CompDoc writes its warnings to logfile, stdout by default.
        directory = CompDoc(mem, logfile=io.StringIO()).dirlist
    except OSError:
        raise
    except Exception:  # noqa: BLE001 — a damaged compound file is simply not an encrypted document
        return False
    return any(entry.name == "EncryptedPackage" and entry.etype == _OLE_STREAM for entry in directory)


# A directory entry's object type for a stream (MS-CFB 2.6.1).
_OLE_STREAM = 2


class ContentBuffer:
    def __init__(self, max_bytes: int):
        self.max_content_size = max_bytes
        self.content_buffer = deque()
        self.buffer_length = 0
        self.total_bytes = 0

    def append_content(self, line: str) -> None:
        """Appends a line of text to the content buffer"""

        line = self.replace_chars(line)
        words = line.split()
        self.total_bytes += len(line)

        for word in words:
            self.content_buffer.append(word)
            self.buffer_length += len(word) + 1

    def content_buffer_full(self) -> bool:
        """Returns True if the content buffer is full, False otherwise"""

        return self.buffer_length >= self.max_content_size

    def replace_chars(self, content: str) -> str:
        """Replaces characters in a string to make it simpler for data handler regexes.
        Replaces newlines, carriage returns, and tabs with spaces.  Strips the string of leading and trailing whitespace."""

        return content.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()

    def get_content(self) -> str:
        """Returns up to max_content_size amount of data from the buffer"""

        content: list = []
        content_length: int = 0

        while self.content_buffer and content_length < self.max_content_size:
            word = self.content_buffer.popleft()
            content.append(word)
            self.buffer_length -= len(word) + 1
            content_length += len(word) + 1

        return " ".join(content)

    def finalize_content(self) -> str:
        """Returns the remaining content in the buffer"""

        content = " ".join(self.content_buffer)
        self.buffer_length = 0
        self.content_buffer = deque()

        return content
