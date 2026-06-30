#!/usr/bin/env python3
"""Create PIIDigger 7z archive test fixtures.

Run from the repository root:
    python testdata/7z/create_fixtures.py

Requires py7zr (pip install py7zr or uv sync --extra dev).
All fixtures are deterministic and committed to the repository.
Re-running regenerates them in place.
"""
from __future__ import annotations

import io
from pathlib import Path

import py7zr

_HERE = Path(__file__).parent


def _write(name: str, data: bytes) -> None:
    path = _HERE / name
    path.write_bytes(data)
    print(f"  {path.name}  ({len(data):,} bytes)")


# ---------------------------------------------------------------------------
# Fixture generators
# ---------------------------------------------------------------------------


def _simple_pii() -> None:
    """One plaintext member containing a known PAN (Visa test number)."""
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(b"card number: 4111111111111111\n", "readme.txt")
    _write("simple-pii.7z", buf.getvalue())


def _many_members() -> None:
    """5 plaintext members — tests configure max_members=3 to exercise the limit."""
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        for i in range(1, 6):
            szf.writestr(f"content {i}".encode(), f"file{i:02d}.txt")
    _write("many-members.7z", buf.getvalue())


def _oversize_member() -> None:
    """Member whose uncompressed size exceeds the default 64 MB limit.

    LZMA2 compresses 100 MB of zeros to ~1 KB, so the archive file stays small.
    The ENUM handler rejects the member at check 4 (individual size limit)
    before any extraction is attempted.
    """
    data = b"\x00" * (100 * 1024 * 1024)
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        szf.writestr(data, "big-file.txt")
    _write("oversize-member.7z", buf.getvalue())


def _corrupt() -> None:
    """Invalid bytes — triggers an exception when py7zr tries to open it.

    Does not start with the 7z magic (37 7A BC AF 27 1C) so is_7zfile()
    also returns False.
    """
    _write("corrupt.7z", b"this is not a valid 7z file at all!!!")


def _encrypted() -> None:
    """Archive-level password-protected 7z.

    py7zr supports writing encrypted archives with a password.
    SevenZipFile.needs_password() returns True for this fixture.
    """
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w", password="test-password") as szf:
        szf.writestr(b"secret content", "secret.txt")
    _write("encrypted.7z", buf.getvalue())


# ---------------------------------------------------------------------------
# Verify fixtures are readable by py7zr after creation
# ---------------------------------------------------------------------------


def _verify() -> None:
    print("  Verifying fixtures...")

    with py7zr.SevenZipFile(_HERE / "simple-pii.7z", "r") as szf:
        members = szf.list()
        assert any(m.filename == "readme.txt" for m in members), "simple-pii member missing"

    with py7zr.SevenZipFile(_HERE / "many-members.7z", "r") as szf:
        members = szf.list()
        assert len(members) == 5, f"many-members: expected 5 members, got {len(members)}"

    with py7zr.SevenZipFile(_HERE / "oversize-member.7z", "r") as szf:
        members = szf.list()
        assert members[0].uncompressed == 100 * 1024 * 1024, "oversize-member: unexpected size"

    assert py7zr.is_7zfile(_HERE / "corrupt.7z") is False, "corrupt.7z should not be a valid 7z file"

    with py7zr.SevenZipFile(_HERE / "encrypted.7z", password="test-password") as szf:
        assert szf.needs_password(), "encrypted.7z should report needs_password()"

    print("  All fixtures verified OK.")


def main() -> None:
    print(f"Writing fixtures to {_HERE}")
    _simple_pii()
    _many_members()
    _oversize_member()
    _corrupt()
    _encrypted()
    _verify()
    print("Done.")


if __name__ == "__main__":
    main()
