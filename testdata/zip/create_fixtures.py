#!/usr/bin/env python3
"""Create PIIDigger Phase 5 test fixture ZIP files.

Run from the repository root:
    python testdata/zip/create_fixtures.py

All fixtures are deterministic (fixed timestamps, no random content) and
committed to the repository.  Re-running this script regenerates them in place.
"""
from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

_HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# Raw ZIP builder — for fixtures needing non-standard member attributes
# ---------------------------------------------------------------------------


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _raw_deflate(data: bytes) -> bytes:
    """Compress with raw DEFLATE (no zlib header/trailer) as ZIP expects."""
    obj = zlib.compressobj(level=9, wbits=-15)
    return obj.compress(data) + obj.flush()


def _build_raw_zip(
    members: list[tuple[str, bytes, int, int, int]],
) -> bytes:
    """Build minimal valid ZIP bytes from member descriptors.

    Each tuple: (member_name, content, method, flag_bits, fake_uncompressed_size).
    - method: 0=STORED, 8=DEFLATED
    - flag_bits: 0x1 = traditional encryption bit
    - fake_uncompressed_size: 0 = use len(content); >0 = override both local and
      central-directory uncompressed-size fields.

    The fake size is written to BOTH the local header and the central directory
    so Python's ZipFile.infolist() returns the manipulated value.  Attempting to
    actually decompress such a member yields garbage; these fixtures are used
    only to exercise ENUM-stage safety checks that reject the member before any
    extraction is attempted.
    """
    buf = io.BytesIO()
    cd_entries: list[bytes] = []

    for name_str, content, method, flag_bits, fake_uncomp in members:
        name = name_str.encode("utf-8")
        offset = buf.tell()

        compressed = _raw_deflate(content) if method == 8 else content
        crc = _crc32(content)
        comp_size = len(compressed)
        uncomp_size = fake_uncomp if fake_uncomp > 0 else len(content)

        # Local file header: 30 bytes (signature + 10 fixed fields) + filename
        local_hdr = struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04",
            20, flag_bits, method, 0, 0,
            crc, comp_size, uncomp_size,
            len(name), 0,
        )
        buf.write(local_hdr + name + compressed)

        # Central directory entry: 46 bytes + filename
        cd_entry = struct.pack(
            "<4sHHHHHHIIIHHHHHII",
            b"PK\x01\x02",
            20, 20, flag_bits, method, 0, 0,
            crc, comp_size, uncomp_size,
            len(name), 0, 0, 0, 0, 0,
            offset,
        )
        cd_entries.append(cd_entry + name)

    cd_offset = buf.tell()
    for entry in cd_entries:
        buf.write(entry)
    cd_size = buf.tell() - cd_offset

    # End of central directory: 22 bytes
    eocd = struct.pack(
        "<4sHHHHIIH",
        b"PK\x05\x06",
        0, 0,
        len(cd_entries), len(cd_entries),
        cd_size, cd_offset,
        0,
    )
    buf.write(eocd)
    return buf.getvalue()


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
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", "card number: 4111111111111111\n")
    _write("simple-pii.zip", buf.getvalue())


def _nested_depth_2() -> None:
    """outer.txt + inner.zip (which contains inner.txt)."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inner.txt", "inner content")

    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("outer.txt", "outer content")
        zf.writestr("inner.zip", inner.getvalue())
    _write("nested-depth-2.zip", outer.getvalue())


def _oversize_member() -> None:
    """Single member with a faked 200 MB uncompressed size in both headers.

    Actual content is 17 bytes; the headers are manipulated via _build_raw_zip.
    The ENUM handler rejects the member at the size check before any extraction
    is attempted, so the header mismatch never causes a read error.
    """
    data = _build_raw_zip([
        ("big-file.txt", b"tiny real content", 0, 0, 200 * 1024 * 1024),
    ])
    # Verify ZipFile can open it and sees the faked size
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        info = zf.getinfo("big-file.txt")
        if info.file_size != 200 * 1024 * 1024:
            raise RuntimeError(f"unexpected file_size={info.file_size}")
    _write("oversize-member.zip", data)


def _many_members() -> None:
    """5 plaintext members — tests configure max_members=3 to exercise the limit."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(1, 6):
            zf.writestr(f"file{i:02d}.txt", f"content {i}")
    _write("many-members.zip", buf.getvalue())


def _encrypted_member() -> None:
    """One member with the traditional-encryption flag bit set (0x1).

    Python's zipfile module cannot write encrypted members natively; the flag
    is injected via raw ZIP construction.
    """
    data = _build_raw_zip([
        ("secret.txt", b"encrypted content placeholder", 0, 0x1, 0),
    ])
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        info = zf.getinfo("secret.txt")
        if not (info.flag_bits & 0x1):
            raise RuntimeError("encryption flag was not set")
    _write("encrypted-member.zip", data)


def _corrupt() -> None:
    """Invalid bytes — triggers BadZipFile when ZipFile tries to open it."""
    _write("corrupt.zip", b"PK\x03\x04this is not a valid ZIP file at all!!!")


def _traversal_member() -> None:
    """Member whose path contains '..' — rejected by path-traversal check.

    Python's zipfile.ZipFile.writestr() does not sanitise ZipInfo filenames;
    raw construction is used to guarantee the traversal path survives into
    the central directory regardless of the Python version.
    """
    data = _build_raw_zip([
        ("../traversal.txt", b"traversal content", 0, 0, 0),
    ])
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        names = zf.namelist()
        if "../traversal.txt" not in names:
            raise RuntimeError(f"traversal path missing from {names}")
    _write("traversal-member.zip", data)


def _zip_bomb_simulated() -> None:
    """Real deflate of 2 MB of zeros → ~1 KB compressed; ratio > 1000:1.

    Uses genuine DEFLATE compression at maximum level.  No header manipulation
    is needed: zeros compress extremely well, and the resulting ratio reliably
    exceeds 1000:1 with any compliant deflate implementation.
    """
    bomb_content = b"\x00" * (2 * 1024 * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        info = zipfile.ZipInfo("bomb.txt")
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, bomb_content)

    data = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        info = zf.getinfo("bomb.txt")
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio <= 1000:
            raise RuntimeError(
                f"bomb ratio {ratio:.0f}:1 is not > 1000:1 — increase content or compression level"
            )
        print(f"    bomb.txt: {info.file_size:,} / {info.compress_size:,} = {ratio:.0f}:1")
    _write("zip-bomb-simulated.zip", data)


def main() -> None:
    print(f"Writing fixtures to {_HERE}")
    _simple_pii()
    _nested_depth_2()
    _oversize_member()
    _many_members()
    _encrypted_member()
    _corrupt()
    _traversal_member()
    _zip_bomb_simulated()
    print("Done.")


if __name__ == "__main__":
    main()
