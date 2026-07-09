from pathlib import Path

import pytest

from piidigger.getencoding import detect_encoding


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
