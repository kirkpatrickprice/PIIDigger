#!/usr/bin/env python3
"""Create PIIDigger tar archive test fixtures.

Run from the repository root:
    python testdata/tar/create_fixtures.py

All fixtures are deterministic and committed to the repository.
Re-running regenerates them in place.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

_HERE = Path(__file__).parent

_PII_CONTENT = b"card number: 4111111111111111\n"


def _write(name: str, data: bytes) -> None:
    path = _HERE / name
    path.write_bytes(data)
    print(f"  {path.name}  ({len(data):,} bytes)")


def _make_tar(compression: str, members: list[tuple[str, bytes]]) -> bytes:
    """Return tar archive bytes.  compression: '' | 'gz' | 'bz2' | 'xz'."""
    mode = f"w:{compression}" if compression else "w:"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixture generators
# ---------------------------------------------------------------------------


def _simple_pii() -> None:
    """One plaintext member containing a known PAN — happy path for all four flavors."""
    member = [("readme.txt", _PII_CONTENT)]
    _write("simple-pii.tar",     _make_tar("",    member))
    _write("simple-pii.tar.gz",  _make_tar("gz",  member))
    _write("simple-pii.tar.bz2", _make_tar("bz2", member))
    _write("simple-pii.tar.xz",  _make_tar("xz",  member))


def _many_members() -> None:
    """5 plaintext members — tests configure max_members=3 to exercise the limit."""
    members = [(f"file{i:02d}.txt", f"content {i}".encode()) for i in range(1, 6)]
    _write("many-members.tar", _make_tar("", members))


def _oversize_member() -> None:
    """Member whose uncompressed size exceeds a low limit when configured.

    100 MB of zeros compresses to a few KB under gzip, so the archive stays
    small on disk.  The ENUM handler rejects the member at the individual size
    check before any extraction is attempted.
    """
    data = b"\x00" * (100 * 1024 * 1024)
    _write("oversize-member.tar.gz", _make_tar("gz", [("big-file.txt", data)]))


def _corrupt() -> None:
    """Invalid bytes — triggers TarError when tarfile tries to open it."""
    _write("corrupt.tar", b"this is not a valid tar archive!!!")


def _traversal_member() -> None:
    """Archive containing a path-traversal member name (../traversal.txt).

    tarfile.TarInfo does not sanitise member names, so this can be constructed
    directly without any raw-byte manipulation (contrast with ZIP, which
    requires manual central-directory patching).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        info = tarfile.TarInfo(name="../traversal.txt")
        data = b"should be rejected\n"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        # Include a clean member so the archive isn't completely trivial
        good = tarfile.TarInfo(name="clean.txt")
        good_data = b"clean content\n"
        good.size = len(good_data)
        tf.addfile(good, io.BytesIO(good_data))
    _write("traversal-member.tar", buf.getvalue())


def _symlink_member() -> None:
    """Archive with a regular file plus a symlink member.

    The symlink should be excluded by list_members() (Decision 5) and
    never produce a SCAN_ARCHIVE_MEMBER task.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        regular = tarfile.TarInfo(name="readme.txt")
        data = b"regular content\n"
        regular.size = len(data)
        tf.addfile(regular, io.BytesIO(data))
        link = tarfile.TarInfo(name="link-to-readme.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "readme.txt"
        link.size = 0
        tf.addfile(link)
    _write("symlink-member.tar", buf.getvalue())


# ---------------------------------------------------------------------------
# Verify fixtures are readable by tarfile after creation
# ---------------------------------------------------------------------------


def _verify() -> None:
    print("  Verifying fixtures...")

    for name in ("simple-pii.tar", "simple-pii.tar.gz", "simple-pii.tar.bz2", "simple-pii.tar.xz"):
        with tarfile.open(_HERE / name, mode="r:*") as tf:
            names = tf.getnames()
            if "readme.txt" not in names:
                raise RuntimeError(f"{name}: readme.txt member missing (got {names})")

    with tarfile.open(_HERE / "many-members.tar", mode="r:*") as tf:
        if len(tf.getmembers()) != 5:
            raise RuntimeError(f"many-members: expected 5 members, got {len(tf.getmembers())}")

    with tarfile.open(_HERE / "oversize-member.tar.gz", mode="r:*") as tf:
        members = tf.getmembers()
        if members[0].size != 100 * 1024 * 1024:
            raise RuntimeError("oversize-member: unexpected uncompressed size")

    try:
        with tarfile.open(_HERE / "corrupt.tar", mode="r:*") as tf:
            tf.getmembers()
        raise RuntimeError("corrupt.tar should have raised TarError")
    except tarfile.TarError:
        pass

    with tarfile.open(_HERE / "traversal-member.tar", mode="r:*") as tf:
        names = tf.getnames()
        if "../traversal.txt" not in names:
            raise RuntimeError(f"traversal-member: expected '../traversal.txt', got {names}")

    with tarfile.open(_HERE / "symlink-member.tar", mode="r:*") as tf:
        members = tf.getmembers()
        types = {m.name: m.type for m in members}
        if types.get("link-to-readme.txt") != tarfile.SYMTYPE:
            raise RuntimeError("symlink-member: expected a SYMTYPE member")

    print("  All fixtures verified OK.")


def main() -> None:
    print(f"Writing fixtures to {_HERE}")
    _simple_pii()
    _many_members()
    _oversize_member()
    _corrupt()
    _traversal_member()
    _symlink_member()
    _verify()
    print("Done.")


if __name__ == "__main__":
    main()
