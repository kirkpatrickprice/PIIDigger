"""Tests for open_with_progress(): which reads count as progress, and the buffer sizes.

Most tests read through stream.raw, so each read reaches the disk exactly as
written; the BufferedReader in front would otherwise merge or skip reads.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from piidigger._progress_io import _ARCHIVE_BUFFER_SIZE, _BLOCK_SIZE, open_with_progress
from piidigger.orchestration.sources import FilesystemItem


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def _file(tmp_path: Path, blocks: int) -> Path:
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x5a" * (blocks * _BLOCK_SIZE))
    return path


def _read_blocks(raw: io.RawIOBase, first: int, count: int) -> None:
    """Read count whole blocks from block first, one block per read."""
    raw.seek(first * _BLOCK_SIZE)
    for _ in range(count):
        assert raw.read(_BLOCK_SIZE)


@pytest.mark.unit
def test_reading_front_to_back_reports_progress(tmp_path: Path) -> None:
    counter = _Counter()

    with open_with_progress(_file(tmp_path, 4), counter, new_bytes_only=True) as stream:
        _read_blocks(stream.raw, 0, 4)

    assert counter.calls == 4


@pytest.mark.unit
def test_re_reading_the_same_region_is_not_progress(tmp_path: Path) -> None:
    """A parser looping over bytes it has already read must not keep its task alive."""
    counter = _Counter()

    with open_with_progress(_file(tmp_path, 64), counter, new_bytes_only=True) as stream:
        for _ in range(5):
            _read_blocks(stream.raw, 0, 32)  # the same 2 MiB, every pass

    assert counter.calls == 32, "only the first pass reads new blocks"


@pytest.mark.unit
def test_a_read_touching_one_new_block_reports(tmp_path: Path) -> None:
    counter = _Counter()

    with open_with_progress(_file(tmp_path, 4), counter, new_bytes_only=True) as stream:
        raw = stream.raw
        _read_blocks(raw, 0, 1)
        raw.seek(_BLOCK_SIZE // 2)
        raw.read(_BLOCK_SIZE)  # half of block 0, half of block 1
        after_overlap = counter.calls
        _read_blocks(raw, 0, 2)  # both already read

    assert after_overlap == 2
    assert counter.calls == 2


@pytest.mark.unit
def test_archive_default_counts_every_read(tmp_path: Path) -> None:
    """Archive readers go front to back, so they skip the bookkeeping and every read counts."""
    counter = _Counter()

    with open_with_progress(_file(tmp_path, 4), counter) as stream:
        for _ in range(3):
            _read_blocks(stream.raw, 0, 1)

    assert counter.calls == 3


@pytest.mark.unit
def test_bytes_appended_after_opening_count_as_new(tmp_path: Path) -> None:
    path = _file(tmp_path, 2)
    counter = _Counter()

    with open_with_progress(path, counter, new_bytes_only=True) as stream:
        _read_blocks(stream.raw, 0, 2)
        with path.open("ab") as grow:
            grow.write(b"\x5a" * (20 * _BLOCK_SIZE))  # well past the bitmap sized at open
        _read_blocks(stream.raw, 2, 20)

    assert counter.calls == 22


@pytest.mark.unit
def test_reads_at_end_of_file_are_not_progress(tmp_path: Path) -> None:
    counter = _Counter()

    with open_with_progress(_file(tmp_path, 1), counter, new_bytes_only=True) as stream:
        _read_blocks(stream.raw, 0, 1)
        for _ in range(10):
            assert stream.raw.read(_BLOCK_SIZE) == b""

    assert counter.calls == 1


def _buffer_size(stream: io.BufferedReader) -> int:
    """BufferedReader does not expose its buffer size; a one-byte read fills exactly one buffer."""
    stream.seek(0)
    stream.read(1)
    return len(stream.peek(1)) + 1


@pytest.mark.unit
def test_open_stream_uses_the_default_buffer_and_counts_only_new_bytes(tmp_path: Path) -> None:
    """pypdf and openpyxl seek around, and every seek outside the buffer refills all of it."""
    path = _file(tmp_path, 48)  # 3 MiB, larger than either buffer

    with FilesystemItem(path).open_stream() as stream:
        assert isinstance(stream, io.BufferedReader)
        assert _buffer_size(stream) == io.DEFAULT_BUFFER_SIZE
        assert stream.raw._read_blocks is not None  # type: ignore[attr-defined]


@pytest.mark.unit
def test_archive_readers_keep_the_large_buffer(tmp_path: Path) -> None:
    path = _file(tmp_path, 48)

    with open_with_progress(path) as stream:
        assert _buffer_size(stream) == _ARCHIVE_BUFFER_SIZE
        assert stream.raw._read_blocks is None  # type: ignore[attr-defined]
