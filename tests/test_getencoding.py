import random
from pathlib import Path

import pytest

from piidigger.getencoding import (
    _ENCODING_SAMPLE_BYTES,
    _is_mostly_text,
    _trim_partial_utf8,
    detect_encoding,
    detect_file_encoding,
    remove_nuls,
)


@pytest.mark.utils
@pytest.mark.parametrize(
    "filename, expected",
    [
        ("testdata/pan/sample-pans.json", "ascii"),
        ("testdata/binary-json.json", None),
    ],
)
def test_detect_encoding(filename: str, expected: str | None) -> None:
    data = Path(filename).read_bytes()
    assert detect_encoding(data) == expected


# ---------------------------------------------------------------------------
# detect_file_encoding — the shared answer for the plaintext handler and inspect
# ---------------------------------------------------------------------------

_SAMPLE = _ENCODING_SAMPLE_BYTES


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "sample.txt"
    path.write_bytes(data)
    return path


@pytest.mark.utils
def test_detect_file_encoding_ascii_reported_as_utf8(tmp_path: Path) -> None:
    assert detect_file_encoding(_write(tmp_path, b"plain ascii text\n")) == "utf_8"


@pytest.mark.utils
def test_detect_file_encoding_non_ascii_after_sample(tmp_path: Path) -> None:
    # The sample is pure ASCII; the UTF-8 text past it must still decode, so the answer can't be "ascii".
    data = b"a" * (_SAMPLE + 10) + "José Müller josé@exämple.com\n".encode()
    assert detect_file_encoding(_write(tmp_path, data)) == "utf_8"


@pytest.mark.utils
def test_detect_file_encoding_sample_cut_mid_character(tmp_path: Path) -> None:
    # One leading byte shifts every 2-byte Cyrillic character so the sample boundary splits one.
    data = b"x" + ("Привет" * (_SAMPLE // 12 + 1000)).encode()
    assert (data[_SAMPLE] & 0xC0) == 0x80, "fixture must put a continuation byte right after the sample"
    assert detect_file_encoding(_write(tmp_path, data)) == "utf_8"


@pytest.mark.utils
def test_detect_file_encoding_binary_junk_at_start(tmp_path: Path) -> None:
    # A 64 KiB binary header made a 256 KiB sample undetectable (file skipped).
    # The sample must be large enough for the text after it to win.
    junk = random.Random(3).randbytes(64 * 1024)  # noqa: S311 — seeded for a reproducible fixture, not crypto
    log = b"".join(b'{"req": %d, "user": "user%d@example.com", "status": 200}\n' % (i, i) for i in range(100_000))
    assert len(log) > _SAMPLE
    assert detect_file_encoding(_write(tmp_path, junk + log)) is not None


@pytest.mark.utils
def test_detect_file_encoding_utf16_with_bom(tmp_path: Path) -> None:
    data = ("hello world 4111111111111111\n" * (_SAMPLE // 58 + 1000)).encode("utf-16")
    assert len(data) > _SAMPLE
    assert detect_file_encoding(_write(tmp_path, data)) == "utf_16"


@pytest.mark.utils
def test_detect_file_encoding_undetectable() -> None:
    assert detect_file_encoding(Path("testdata/binary-json.json")) is None


# ---------------------------------------------------------------------------
# _trim_partial_utf8
# ---------------------------------------------------------------------------


@pytest.mark.utils
@pytest.mark.parametrize(
    "sample, expected",
    [
        ("aé".encode()[:-1], b"a"),  # 2-byte char, lead byte only
        ("a€".encode()[:-1], b"a"),  # 3-byte char, 2 of 3 bytes
        ("a€".encode()[:-2], b"a"),  # 3-byte char, 1 of 3 bytes
        ("a😀".encode()[:-1], b"a"),  # 4-byte char, 3 of 4 bytes
        ("a😀".encode()[:-3], b"a"),  # 4-byte char, 1 of 4 bytes
        ("aé".encode(), "aé".encode()),  # complete characters are kept
        ("a€".encode(), "a€".encode()),
        ("a😀".encode(), "a😀".encode()),
        (b"plain ascii", b"plain ascii"),
        (b"", b""),
        (b"\xff\xfe", b"\xff\xfe"),  # UTF-16 LE byte-order mark is not UTF-8
    ],
)
def test_trim_partial_utf8(sample: bytes, expected: bytes) -> None:
    assert _trim_partial_utf8(sample) == expected


# ---------------------------------------------------------------------------
# detect_file_encoding — fallback for mostly-text files detection gives up on
# ---------------------------------------------------------------------------

# Text with scattered NULs and high bytes, as in a browser LevelDB log.
_MOSTLY_TEXT = b"".join(
    b"key%d=jane.doe@example.com; " % i + bytes((i * 37 + j * 91) % 256 for j in range(6)) + b"\0\0" for i in range(300)
)


@pytest.mark.utils
def test_mostly_text_fixture_really_defeats_detection() -> None:
    """Keeps the fallback tests honest: without this, they might never reach the fallback."""
    assert detect_encoding(_MOSTLY_TEXT) is None


@pytest.mark.utils
def test_detect_file_encoding_falls_back_on_mostly_text(tmp_path: Path) -> None:
    assert detect_file_encoding(_write(tmp_path, _MOSTLY_TEXT)) == "latin_1"


@pytest.mark.utils
@pytest.mark.parametrize(
    "fixture",
    ["testdata/binary-json.json", "testdata/plaintext/mislabeled-text-file.txt"],
)
def test_detect_file_encoding_no_fallback_under_half_text(fixture: str) -> None:
    assert detect_file_encoding(Path(fixture)) is None


@pytest.mark.utils
@pytest.mark.parametrize(
    "sample, expected",
    [
        (b"", False),
        (b"\0" * 10, False),
        (b"a" * 10, True),
        (b"\t\r\n" + b"\0" * 3, True),
        (b"a" * 5 + b"\0" * 5, True),
        (b"a" * 49 + b"\0" * 51, False),
        (b"a" * 49 + b"\xff" * 51, False),
        (b"a\0" * 8 + b"\xff" * 10, True),
        (b"\0a" * 8 + b"\xff" * 10, True),
        (b"a\0" * 3 + b"\xff" * 7, False),
    ],
    ids=[
        "empty",
        "all-nul",
        "all-text",
        "whitespace-counts",
        "exactly-half",
        "just-under-half",
        "high-bytes-dont-count",
        "utf16-le-nuls-count",
        "utf16-be-nuls-count",
        "short-run-nuls-dont-count",
    ],
)
def test_is_mostly_text(sample: bytes, expected: bool) -> None:
    assert _is_mostly_text(sample) is expected


@pytest.mark.utils
def test_detect_file_encoding_empty_file_is_still_none(tmp_path: Path) -> None:
    assert detect_file_encoding(_write(tmp_path, b"")) is None


@pytest.mark.utils
@pytest.mark.parametrize(
    "text, expected",
    [
        ("no nuls", "no nuls"),
        ("j\0a\0n\0e\0", "jane"),
        ("\0j\0a\0n\0e", "jane"),
        ("a\0b", "a b"),
        ("a\0b\0c\0", "a b c "),
        ("4111\x005222", "4111 5222"),
        ("key=j\0a\0n\0e\0;x\0y", "key=jane;x y"),
        ("\0\0\0", "   "),
    ],
    ids=["none", "utf16-le", "utf16-be", "delimiter", "short-run", "digits", "mixed", "only-nuls"],
)
def test_remove_nuls(text: str, expected: str) -> None:
    assert remove_nuls(text) == expected
