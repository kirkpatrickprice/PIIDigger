#!/usr/bin/env python3
"""Create JSON-lines log fixtures whose encoding "surprise" starts MBs into the file.

Run from the repository root:
    uv run python -P testdata/plaintext/json-logs/create_fixtures.py

Each fixture starts with ~5.5 MB of plain-ASCII JSON-lines log records (one
email address per record), then introduces something that can change an
encoding guess: non-ASCII text, binary junk, NUL padding, or UTF-16 records.
They exercise how much of a file the plaintext handler's encoding guess needs
to see (see compare_sample_sizes.py in this folder).

All fixtures are deterministic (fixed records, seeded pseudo-random bytes).  
Re-running this script regenerates them in place.  They should NOT be committed
to the repo.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

_HERE = Path(__file__).parent

# ~5.5 MB: past a 4 MiB encoding sample, well inside a 16 MiB one.
_HEAD_RECORDS = 50_000
_TAIL_RECORDS = 5_000


def _log_records(count: int, start: int) -> bytes:
    lines = []
    for i in range(start, start + count):
        record = {
            "ts": f"2026-09-29T12:{i % 60:02d}:00Z",
            "level": "INFO",
            "req": i,
            "user": f"user{i}@example.com",
            "msg": "request processed",
            "status": 200,
        }
        lines.append(json.dumps(record).encode("ascii") + b"\n")
    return b"".join(lines)


def _name_records(encoding: str) -> bytes:
    """200 records carrying a non-ASCII name, encoded with the given codec."""
    return b"".join(
        json.dumps({"user": f"late{i}@example.com", "name": "José Müller"}, ensure_ascii=False).encode(encoding)
        + b"\n"
        for i in range(200)
    )


def _utf16_records(count: int) -> bytes:
    return "".join(f'{{"user": "u16_{i}@example.com"}}\n' for i in range(count)).encode("utf-16-le")


def _binary(size: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(size)


def build_fixtures() -> dict[str, bytes]:
    head = _log_records(_HEAD_RECORDS, 0)
    tail = _log_records(_TAIL_RECORDS, _HEAD_RECORDS)
    return {
        "ascii-baseline.jsonl": head + tail,
        "cp1252-names-after-5mb.jsonl": head + _name_records("cp1252") + tail,
        "utf8-names-after-5mb.jsonl": head + _name_records("utf-8") + tail,
        "nul-block-after-5mb.jsonl": head + bytes(4096) + tail,
        "binary-4kib-after-5mb.jsonl": head + _binary(4096, seed=1) + tail,
        "binary-256kib-after-5mb.jsonl": head + _binary(256 * 1024, seed=2) + tail,
        "nul-padding-at-end.jsonl": head + tail + bytes(1024 * 1024),
        "binary-64kib-at-start.jsonl": _binary(64 * 1024, seed=3) + head + tail,
        "utf16le-records-after-5mb.jsonl": head + _utf16_records(30_000),
    }


def main() -> None:
    for name, data in build_fixtures().items():
        (_HERE / name).write_bytes(data)
        print(f"{name:34} {len(data) / 2**20:5.1f} MiB")


if __name__ == "__main__":
    main()
