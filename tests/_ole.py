"""Build a minimal OLE compound file (MS-CFB, version 3) for tests.

Office saves a password-protected .docx or .xlsx as one of these, holding an
EncryptionInfo and an EncryptedPackage stream, instead of a zip.  This builds
the same structure with placeholder stream contents, so tests need neither a
real encrypted file nor an encryption library.

Every stream is padded to the 4096-byte mini-stream cutoff, so all of them sit
in ordinary 512-byte sectors and the file needs no mini FAT.
"""

from __future__ import annotations

import struct

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_SECTOR = 512
_MINI_CUTOFF = 4096
_FREE = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE
_FAT_SECTOR = 0xFFFFFFFD
_NO_STREAM = 0xFFFFFFFF


def _dir_entry(name: str, kind: int, *, child: int, right: int, start: int, size: int) -> bytes:
    encoded = (name + "\0").encode("utf-16-le")
    return (
        encoded.ljust(64, b"\0")
        + struct.pack("<HBB", len(encoded), kind, 1)  # name length, object type, colour (black)
        + struct.pack("<III", _NO_STREAM, right, child)  # left sibling, right sibling, child
        + bytes(16 + 4 + 8 + 8)  # CLSID, state bits, creation and modified times
        + struct.pack("<IQ", start, size)
    )


def ole_file(streams: dict[str, bytes]) -> bytes:
    """An OLE compound file holding each stream, by name, at the root.  At most three streams."""
    assert 1 <= len(streams) <= 3, "one directory sector holds the root and three streams"
    padded = {name: data.ljust(_MINI_CUTOFF, b"\0") for name, data in streams.items()}

    # Sector 0 is the FAT, sector 1 the directory; the streams follow in order.
    fat = [_FAT_SECTOR, _END_OF_CHAIN]
    starts: list[int] = []
    for data in padded.values():
        count = -(-len(data) // _SECTOR)
        first = len(fat)
        starts.append(first)
        fat.extend(range(first + 1, first + count))
        fat.append(_END_OF_CHAIN)
    assert len(fat) <= _SECTOR // 4, "one FAT sector covers 128 sectors"
    fat_sector = struct.pack(f"<{_SECTOR // 4}I", *fat, *[_FREE] * (_SECTOR // 4 - len(fat)))

    entries = [_dir_entry("Root Entry", 5, child=1, right=_NO_STREAM, start=_END_OF_CHAIN, size=0)]
    for index, (name, data) in enumerate(padded.items(), start=1):
        right = index + 1 if index < len(padded) else _NO_STREAM
        entries.append(_dir_entry(name, 2, child=_NO_STREAM, right=right, start=starts[index - 1], size=len(data)))
    directory = b"".join(entries).ljust(_SECTOR, b"\0")

    header = (
        OLE_SIGNATURE
        + bytes(16)  # CLSID
        + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)  # minor, major version, byte order, sector shifts
        + bytes(6)
        + struct.pack("<IIIIIIIII", 0, 1, 1, 0, _MINI_CUTOFF, _END_OF_CHAIN, 0, _END_OF_CHAIN, 0)
        + struct.pack("<109I", 0, *[_FREE] * 108)  # the first FAT sector is sector 0
    )
    body = b"".join(data.ljust(-(-len(data) // _SECTOR) * _SECTOR, b"\0") for data in padded.values())
    return header + fat_sector + directory + body


def encrypted_office_file() -> bytes:
    """What Word and Excel save for a document encrypted with a password."""
    return ole_file({"EncryptionInfo": b"\x04\x00\x04\x00\x40\x00\x00\x00", "EncryptedPackage": b"\x00" * 16})
