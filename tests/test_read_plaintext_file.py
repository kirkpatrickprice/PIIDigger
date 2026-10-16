import tracemalloc
from pathlib import Path

import pytest

from piidigger.exceptions import UndetectableEncodingError
from piidigger.filehandlers.plaintext import _MAX_LINE_CHARS, PlaintextHandler
from piidigger.getencoding import _ENCODING_SAMPLE_BYTES
from piidigger.models.config import BufferConfig, Config, PlaintextConfig
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path, config: Config | None = None) -> list[str]:
    return list(PlaintextHandler().read(FilesystemItem(path), config or Config()))


# Files whose content should produce no meaningful text output.
# empty-file-utf16le-crlf.txt may yield a bare BOM character (﻿); strip it.
@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "filename",
    [
        "testdata/plaintext/empty-file-utf16le-crlf.txt",
        "testdata/plaintext/unknown-encoding.txt",
        "testdata/plaintext/zero-byte-file.txt",
    ],
)
def test_plaintext_no_meaningful_content(filename: str) -> None:
    chunks = _read(Path(filename))
    content = "".join(chunks).replace("﻿", "").strip()
    assert content == ""


@pytest.mark.filehandlers
def test_plaintext_undetectable_encoding_raises() -> None:
    # A binary header longer than the text after it.  Yielding nothing would make
    # the file a silent skip; the scan handlers log the typed error instead.
    with pytest.raises(UndetectableEncodingError):
        _read(Path("testdata/plaintext/mislabeled-text-file.txt"))


# Files with small, predictable content that fits in a single chunk.
@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "filename, expected",
    [
        (
            "testdata/plaintext/lorem-ipsum-1line-utf8-crlf.txt",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
        ),
        (
            "testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf16le-crlf.txt",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
        ),
        (
            "testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf8-lf.txt",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
        ),
        (
            "testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf8-crlf.txt",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
        ),
        (
            "testdata/plaintext/lorem-ipsum-2line-utf8-crlf-649-bytes.txt",
            "magna fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquam malesuada",
        ),
        (
            "testdata/plaintext/lorem-ipsum-2line-utf8-crlf-650-bytes.txt",
            "magna fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquaam malesuadaa",
        ),
        (
            "testdata/plaintext/lorem-ipsum-2line-utf8-crlf-651-bytes.txt",
            "magnas fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquam malesuadaa",
        ),
        (
            "testdata/plaintext/lorem-ipsum-2line-utf8-crlf-1000-bytes.txt",
            "magna fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquam malesuada bibendum arcu vitae elementum curabitur vitae nunc sed velit dignissim sodales ut eu sem integer vitae justo eget magna fermentum iaculis eu non diam phasellus vestibulum lorem sed risus ultricies tristique nulla aliquet enim tortor at auctor urna nunc id cursus metus aliquam eleifend mi in nulla posuere sollicitudin aliquam ultrices sagittis orci",
        ),
        (
            "testdata/plaintext/random-data-700-bytes.txt",
            "YygE2ENjzFKuEnSjYDQDv6wFPRMbZp8pAd1t3UcGTZxgSq7k7XftmmbbTjcuP0yQLSYkND7VdDJhwqxJES7zRBcLMcDmxBbk1PXuPh3im5hXTB42pPeepAxY3UHTHM56Kjyrz2yYAESWStTHzSr65krBeGTXZNvipfP7PJAMPqpvchjebSta71Rp8ybMKk8idgiHQNWgmMfCRfR61uGx3arFKWeC0xRctv8WdieqPfe7uzE3afprVfTL5E3di8wCkngdPuwnnfPeEBiAbp5RDteqT1Sy5pVWxj0iT9F1qyifEWXbwnvkmcC1D64LBzACXQ5NdhypbdUkr7utz0EupA9FvRNWdSLyeMeychwBN2FWnm0E3XtU2F76RXapcTfz5Y010vfEz8v5EUSbQhxPV4JhpTpeKzYV6a5BARB3AKZ6ChivTmkh8RcMPHpgZhTqex46C8XGZTgZ8zm8QK4mFEbPHY0Qij7BBT4kK1PhxFEKHAdGRqkxwV3Dn186SrpmxrqvBm9wXJh47EKP7BVLjHMZKVMj2n8WZC1x8HcNc1tai2fBC5bMutAR3Cp31WYAr68jui15DUqr949ZLz1amd317ZgBHeaQkZZKceUnV83tpyYtgzjEDN6SxNkx3qGkNnua82YAKHun3N8JDWGPV4mEjzhuHS5Z5KPnQD3K41Yt2zUJwy7vjRZfm0h0",
        ),
    ],
)
def test_plaintext_single_chunk(filename: str, expected: str) -> None:
    chunks = _read(Path(filename))
    assert len(chunks) == 1
    assert chunks[0] == expected


@pytest.mark.filehandlers
def test_plaintext_2paragraph() -> None:
    # With the default buffer size this multi-paragraph file lands in one large chunk.
    chunks = _read(Path("testdata/plaintext/lorem-ipsum-2paragraph-utf8-crlf.txt"))
    content = " ".join(chunks)
    assert "Lorem ipsum dolor sit amet" in content
    assert "Iaculis at erat pellentesque adipiscing" in content


@pytest.mark.filehandlers
def test_plaintext_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/plaintext/does-not-exist.txt"))


@pytest.mark.filehandlers
def test_plaintext_buffer_config_controls_chunking() -> None:
    # A much smaller buffer should split the same file into more, smaller chunks.
    path = Path("testdata/plaintext/lorem-ipsum-2paragraph-utf8-crlf.txt")
    default_chunks = _read(path)
    small_buffer = Config(buffer=BufferConfig(buffer_unit_bytes=5, buffer_unit_count=1))
    small_chunks = _read(path, small_buffer)

    assert len(small_chunks) > len(default_chunks)
    # Same words in the same order regardless of how they were chunked.
    assert " ".join(small_chunks).split() == " ".join(default_chunks).split()


# ---------------------------------------------------------------------------
# Large-file behavior: fixtures are generated in tmp_path rather than committed.
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "generated.txt"
    path.write_bytes(data)
    return path


@pytest.mark.filehandlers
def test_plaintext_non_ascii_after_ascii_sample(tmp_path: Path) -> None:
    # The encoding sample is pure ASCII; UTF-8 text past it must still decode intact.
    filler = b"plain ascii log line user=jdoe status=200\n" * (_ENCODING_SAMPLE_BYTES // 40 + 100)
    path = _write(tmp_path, filler + "José Müller josé@exämple.com\n".encode())

    content = " ".join(_read(path))

    assert "josé@exämple.com" in content
    assert "�" not in content


@pytest.mark.filehandlers
def test_plaintext_sample_cut_mid_character(tmp_path: Path) -> None:
    # One leading byte shifts every 2-byte Cyrillic character so the sample boundary splits one.
    data = b"x" + ("Привет" * (_ENCODING_SAMPLE_BYTES // 12 + 1000)).encode()
    assert (data[_ENCODING_SAMPLE_BYTES] & 0xC0) == 0x80, "fixture must split a character at the sample boundary"

    content = " ".join(_read(_write(tmp_path, data)))

    assert "Привет" in content
    assert "�" not in content


@pytest.mark.filehandlers
def test_plaintext_stops_at_max_scan_mb(tmp_path: Path) -> None:
    filler = b"filler line with nothing interesting in it at all\n"
    body = filler * (2 * 1024 * 1024 // len(filler))
    path = _write(tmp_path, b"EARLYMARKER\n" + body + b"LATEMARKER\n")
    config = Config(plaintext=PlaintextConfig(max_scan_mb=1))

    content = " ".join(_read(path, config))

    assert "EARLYMARKER" in content
    assert "LATEMARKER" not in content


@pytest.mark.filehandlers
def test_plaintext_line_longer_than_readline_cap(tmp_path: Path) -> None:
    # One newline-free line spanning several readline() pieces.  Each token plus its
    # space is 8 characters and _MAX_LINE_CHARS is a multiple of 8, so piece
    # boundaries fall between tokens and every token must come through intact.
    tokens = [f"w{i:06d}" for i in range(_MAX_LINE_CHARS * 3 // 16)]
    line = " ".join(tokens) + " "
    assert len(line) > _MAX_LINE_CHARS
    path = _write(tmp_path, line.encode("ascii"))

    assert " ".join(_read(path)).split() == tokens


@pytest.mark.filehandlers
def test_plaintext_memory_does_not_scale_with_file_size(tmp_path: Path) -> None:
    line = b"2026-09-29 12:00:00 INFO worker processed request user=jdoe@example.com status=200\n"
    path = _write(tmp_path, line * (16 * 1024 * 1024 // len(line)))
    size = path.stat().st_size
    small_buffer = Config(buffer=BufferConfig(buffer_unit_bytes=650, buffer_unit_count=100))

    tracemalloc.start()
    try:
        for _ in PlaintextHandler().read(FilesystemItem(path), small_buffer):
            pass
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # Encoding detection is a fixed cost: the sample plus charset-normalizer's
    # copy of it (~2x the sample).  Everything else must stay small.  Reading the
    # whole file at once peaked at >= 3.49x the file size (~58 MiB here).
    detection_allowance = 3 * _ENCODING_SAMPLE_BYTES
    assert peak < detection_allowance + size / 4
