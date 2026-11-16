"""terminal_text() and escape_std_streams(): file names written to the terminal."""

from __future__ import annotations

import io
import sys

import pytest

from piidigger.orchestration.escaping import escape_std_streams, terminal_text

# A surrogate escape is the byte it stands for on POSIX; on Windows it is part of the UTF-16 name.
_E9 = "\\xe9" if sys.getfilesystemencodeerrors() == "surrogateescape" else "\\udce9"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "drawn"),
    [
        ("/scan/caf\udce9.txt", f"/scan/caf{_E9}.txt"),  # POSIX name that is not UTF-8
        ("/scan/a\x1b[2Jb.txt", "/scan/a\\x1b[2Jb.txt"),  # escape sequence would clear the screen
        ("/scan/line\nbreak.txt", "/scan/line\\x0abreak.txt"),  # newline would split the row
        ("Screenshot 3.45.12\u202fPM.png", "Screenshot 3.45.12\u202fPM.png"),  # macOS puts U+202F in screenshot names
        ("a\u00a0b.txt", "a\u00a0b.txt"),  # no-break space moves nothing: kept, so the name matches the disk
        ("a\u2028b.txt", "a\\u2028b.txt"),  # line separator splits the line: escaped
        ("a\u202eb.txt", "a\\u202eb.txt"),  # right-to-left override reverses the rest: escaped
        ("/scan/café 字.txt", "/scan/café 字.txt"),  # printable non-ASCII stays as it is
    ],
)
def test_terminal_text_escapes_what_a_terminal_cannot_take(raw: str, drawn: str) -> None:
    assert terminal_text(raw) == drawn
    drawn.encode("utf-8")  # strict: a terminal can always encode the result


@pytest.mark.unit
def test_escape_std_streams_lets_a_strict_stream_take_an_undecodable_name(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")  # strict, as a UTF-8 terminal is
    err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    escape_std_streams()
    print("caf\udce9.txt")  # noqa: T201
    print("caf\udce9.txt", file=sys.stderr)  # noqa: T201
    out.flush()
    err.flush()

    expected = f"caf{_E9}.txt"
    assert out.buffer.getvalue().decode("utf-8").strip() == expected  # type: ignore[attr-defined]
    assert err.buffer.getvalue().decode("utf-8").strip() == expected  # type: ignore[attr-defined]


@pytest.mark.unit
def test_escape_std_streams_leaves_a_stream_it_cannot_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stand_in)

    escape_std_streams()

    assert sys.stdout is stand_in
