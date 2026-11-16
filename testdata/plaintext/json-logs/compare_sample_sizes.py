#!/usr/bin/env python3
"""Compare encoding guesses across sample sizes for the JSON-log fixtures.

Run from the repository root (after create_fixtures.py):
    uv run python -P testdata/plaintext/json-logs/compare_sample_sizes.py

For each fixture and each sample size, prints the encoding the plaintext
handler would pick from a sample that size, and how many of the file's ASCII
email addresses survive decoding the whole file with that encoding.  "file
SKIPPED" means no encoding was found, so the plaintext handler yields nothing.

Mirrors getencoding.detect_file_encoding() with a variable sample size: the
sample is trimmed of a cut-off UTF-8 character, and "ascii" becomes "utf_8".

The email count covers ASCII emails only.  In utf16le-records-after-5mb.jsonl
the 30,000 UTF-16 emails are not counted: no single encoding can decode both
the ASCII head and the UTF-16 records, so they are lost at every sample size.
"""

from __future__ import annotations

import re
from pathlib import Path

from piidigger.getencoding import _trim_partial_utf8, detect_encoding

_HERE = Path(__file__).parent
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")
_MIB = 1024 * 1024
_SAMPLE_SIZES: list[tuple[str, int | None]] = [
    ("256 KiB", 256 * 1024),
    ("1 MiB", _MIB),
    ("4 MiB", 4 * _MIB),
    ("16 MiB", 16 * _MIB),
    ("64 MiB", 64 * _MIB),
    ("whole file", None),
]


def guess(data: bytes, sample_bytes: int | None) -> str | None:
    sample = data
    if sample_bytes is not None and len(data) > sample_bytes:
        sample = _trim_partial_utf8(data[:sample_bytes])
    enc = detect_encoding(sample)
    return "utf_8" if enc == "ascii" else enc


def main() -> None:
    for path in sorted(_HERE.glob("*.jsonl")):
        data = path.read_bytes()
        # latin-1 maps every byte to a character, so every ASCII email is countable.
        truth = len(_EMAIL.findall(data.decode("latin-1")))
        print(f"\n{path.name}  ({len(data) / _MIB:.1f} MiB, {truth} ASCII-matchable emails)")
        for label, size in _SAMPLE_SIZES:
            enc = guess(data, size)
            if enc is None:
                print(f"   {label:10} -> {'None':12} file SKIPPED")
                continue
            found = len(_EMAIL.findall(data.decode(enc, errors="replace")))
            print(f"   {label:10} -> {enc:12} {found}/{truth} emails ({100 * found / truth:.1f}%)")


if __name__ == "__main__":
    main()
