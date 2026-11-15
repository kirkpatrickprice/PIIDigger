"""Real-shaped samples of files that are not the user's content, and of user files named like them."""

from __future__ import annotations

import io
import struct

import openpyxl


def appledouble_bytes() -> bytes:
    """An AppleDouble file as macOS writes it beside a file on FAT or a network share (RFC 1740).

    Header: magic, version 2, 16 filler bytes, and an entry count.  Then one
    entry descriptor (Finder info, id 9) and its 32 bytes.
    """
    header = struct.pack(">II16sH", 0x00051607, 0x00020000, b"Mac OS X        ", 1)
    entry_offset = len(header) + 12
    descriptor = struct.pack(">III", 9, entry_offset, 32)
    return header + descriptor + b"TEXTttxt" + bytes(24)


def office_lock_bytes(user: str = "randy") -> bytes:
    """A Word or Excel lock ("owner") file: the user's name, once in ANSI and once in UTF-16, 162 bytes."""
    ansi = bytes([len(user)]) + user.encode("ascii").ljust(53, b" ")
    wide = struct.pack("<H", len(user)) + user.encode("utf-16-le").ljust(106, b"\x00")
    return ansi + wide


def xlsx_bytes(value: str) -> bytes:
    """A real one-cell workbook."""
    wb = openpyxl.Workbook()
    wb.active["A1"] = value  # type: ignore[index]
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
