import random
from pathlib import Path

import pytest

from piidigger.getencoding import _ENCODING_SAMPLE_BYTES, _trim_partial_utf8, detect_encoding, detect_file_encoding


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
