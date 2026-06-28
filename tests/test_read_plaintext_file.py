from pathlib import Path

import pytest

from piidigger.filehandlers.plaintext import PlaintextHandler
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path) -> list[str]:
    return list(PlaintextHandler().read(FilesystemItem(path)))


# Files whose content should produce no meaningful text output.
# empty-file-utf16le-crlf.txt may yield a bare BOM character (﻿); strip it.
@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "filename",
    [
        "testdata/plaintext/empty-file-utf16le-crlf.txt",
        "testdata/plaintext/mislabeled-text-file.txt",
        "testdata/plaintext/unknown-encoding.txt",
        "testdata/plaintext/zero-byte-file.txt",
    ],
)
def test_plaintext_no_meaningful_content(filename: str) -> None:
    chunks = _read(Path(filename))
    content = "".join(chunks).replace("﻿", "").strip()
    assert content == ""


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
    # With DEFAULT_CHUNK_COUNT this multi-paragraph file lands in one large chunk.
    chunks = _read(Path("testdata/plaintext/lorem-ipsum-2paragraph-utf8-crlf.txt"))
    content = " ".join(chunks)
    assert "Lorem ipsum dolor sit amet" in content
    assert "Iaculis at erat pellentesque adipiscing" in content


@pytest.mark.filehandlers
def test_plaintext_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/plaintext/does-not-exist.txt"))
