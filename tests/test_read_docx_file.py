import re
import warnings
import zipfile
from pathlib import Path

import pytest

from piidigger.filehandlers.docx import DocxHandler
from piidigger.models.config import Config
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path) -> list[str]:
    return list(DocxHandler().read(FilesystemItem(path), Config()))


@pytest.mark.filehandlers
def test_docx_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/docx/does-not-exist.docx"))


@pytest.mark.filehandlers
def test_docx_empty_file() -> None:
    chunks = _read(Path("testdata/docx/empty-file.docx"))
    assert chunks == []


# All non-empty DOCX fixtures fit in a single chunk with the default buffer size.
# Each expected value is the exact content the handler produces after word-splitting.
@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "filename, expected",
    [
        (
            "testdata/docx/lorem-ipsum-1line-comments.docx",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. COMMENT RESPONSE {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '4', 'created': '2024-03-28T21:21:00Z', 'modified': '2024-03-28T21:23:00Z'}",
        ),
        (
            "testdata/docx/lorem-ipsum-1line-header-footer.docx",
            "Header Text Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Footer text {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '3', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-04-03T19:21:00Z'}",
        ),
        (
            "testdata/docx/lorem-ipsum-1line-heading-toc.docx",
            "Contents Heading 1 1 Heading 1 Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '3', 'created': '2024-03-28T21:19:00Z', 'modified': '2024-03-28T21:21:00Z'}",
        ),
        (
            "testdata/docx/lorem-ipsum-1line-hyperlink.docx",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '2', 'created': '2024-03-28T21:25:00Z', 'modified': '2024-03-28T21:25:00Z'}",
        ),
        (
            "testdata/docx/lorem-ipsum-1line-with-footnote-endnote.docx",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.----footnote1--------endnote1---- footnote1) FOOTNOTE endnote1) ENDNOTE {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '4', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-03-28T21:18:00Z'}",
        ),
        (
            "testdata/docx/lorem-ipsum-1line-with-table.docx",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Cell R1C1 Cell R1C2 Cell R2C1 Cell R2C2 {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '2', 'created': '2024-03-28T21:16:00Z', 'modified': '2024-03-28T21:16:00Z'}",
        ),
        (
            "testdata/docx/lorem-ipsum-1line-wordart.docx",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. WORDART WORDART {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '5', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-03-29T14:31:00Z'}",
        ),
        (
            "testdata/docx/lorem-ipsum-1line.docx",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '2', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-04-01T13:28:00Z'}",
        ),
    ],
)
def test_docx_single_chunk(filename: str, expected: str) -> None:
    chunks = _read(Path(filename))
    assert len(chunks) == 1
    assert chunks[0] == expected


@pytest.mark.filehandlers
def test_docx_2paragraph() -> None:
    # With the default buffer size both paragraphs arrive as one large chunk.
    chunks = _read(Path("testdata/docx/lorem-ipsum-2paragraph.docx"))
    content = " ".join(chunks)
    assert "Lorem ipsum dolor sit amet" in content
    assert "Iaculis at erat pellentesque adipiscing" in content
    assert "Randy Bartels" in content


def _capture_docx_content(monkeypatch: pytest.MonkeyPatch) -> list:
    """Wrap docx2python so the test can inspect the DocxContent the handler opened."""
    import piidigger.filehandlers.docx as docx_module

    captured: list = []
    original = docx_module.docx2python

    def _wrapper(*args, **kwargs):
        content = original(*args, **kwargs)
        captured.append(content)
        return content

    monkeypatch.setattr(docx_module, "docx2python", _wrapper)
    return captured


def _assert_closed(content) -> None:
    # DocxReader.zipf raises ValueError once close() has been called
    with pytest.raises(ValueError, match="closed"):
        _ = content.docx_reader.zipf


@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "filename",
    [
        "testdata/docx/lorem-ipsum-1line-comments.docx",
        "testdata/docx/empty-file.docx",
    ],
)
def test_docx_closes_zipfile_after_read(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    captured = _capture_docx_content(monkeypatch)
    _read(Path(filename))
    assert len(captured) == 1
    _assert_closed(captured[0])


@pytest.mark.filehandlers
def test_docx_closes_zipfile_when_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_docx_content(monkeypatch)
    gen = DocxHandler().read(FilesystemItem(Path("testdata/docx/lorem-ipsum-1line.docx")), Config())
    next(gen)
    gen.close()
    _assert_closed(captured[0])


def _docx_with_rewritten_parts(tmp_path: Path, rewrites: dict[str, tuple[bytes, bytes]]) -> Path:
    """Copy the comments fixture, replacing bytes in the named zip parts."""
    path = tmp_path / "rewritten.docx"
    with (
        zipfile.ZipFile("testdata/docx/lorem-ipsum-1line-comments.docx") as source,
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename in rewrites:
                pattern, replacement = rewrites[info.filename]
                data, count = re.subn(pattern, replacement, data)
                assert count, f"nothing to rewrite in {info.filename}"
            target.writestr(info, data)
    return path


@pytest.mark.filehandlers
def test_docx_without_core_properties_reads_quietly(tmp_path: Path) -> None:
    """Google Docs writes no docProps/core.xml.  docx2python's warning about it once reached the log with no file name."""
    path = tmp_path / "google-docs.docx"
    with (
        zipfile.ZipFile("testdata/docx/lorem-ipsum-1line.docx") as source,
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename == "docProps/core.xml":
                continue
            data = source.read(info.filename)
            if info.filename == "_rels/.rels":
                data, count = re.subn(rb"<Relationship [^>]*core-properties[^>]*/>", b"", data)
                assert count == 1
            target.writestr(info, data)
    item = FilesystemItem(path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        content = " ".join(DocxHandler().read(item, Config()))

    assert "Lorem ipsum dolor sit amet" in content
    assert item.unread_reason is None, "a missing core-properties file loses nothing"
    assert not [w for w in caught if "core-properties" in str(w.message)]


@pytest.mark.filehandlers
def test_docx_comment_with_no_date_keeps_the_body_and_reports_comments(tmp_path: Path) -> None:
    """docx2python reads w:date unconditionally, and the body is still only buffered when it raises."""
    path = _docx_with_rewritten_parts(
        tmp_path,
        {
            "word/comments.xml": (rb' w:date="[^"]*"', b""),
            "word/document.xml": (rb"Lorem ipsum", b"4893013335386137 Lorem ipsum"),
        },
    )
    item = FilesystemItem(path)

    content = " ".join(DocxHandler().read(item, Config()))

    assert "4893013335386137" in content
    assert "Randy Bartels" in content, "the document properties after the comments were lost"
    assert item.unread_reason == "Word comments could not be read"


@pytest.mark.filehandlers
def test_docx_bad_properties_keeps_body_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    from docx2python.docx_output import DocxContent

    def broken(self):
        raise KeyError("dc:creator")

    monkeypatch.setattr(DocxContent, "core_properties", property(broken))
    item = FilesystemItem(Path("testdata/docx/lorem-ipsum-1line-comments.docx"))

    content = " ".join(DocxHandler().read(item, Config()))

    assert "Lorem ipsum" in content
    assert item.unread_reason == "Word document properties could not be read"


@pytest.mark.filehandlers
def test_docx_reports_every_unread_part_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from docx2python.docx_output import DocxContent

    def broken(self):
        raise KeyError("part")

    monkeypatch.setattr(DocxContent, "comments", property(broken))
    monkeypatch.setattr(DocxContent, "core_properties", property(broken))
    item = FilesystemItem(Path("testdata/docx/lorem-ipsum-1line-comments.docx"))

    assert "Lorem ipsum" in " ".join(DocxHandler().read(item, Config()))
    assert item.unread_reason == "Word comments and document properties could not be read"


def _docx_with_bad_crc(tmp_path: Path, part: str) -> Path:
    """The comments fixture with a PAN in its body and one byte of the named part's data flipped."""
    path = tmp_path / "bad-crc.docx"
    with (
        zipfile.ZipFile("testdata/docx/lorem-ipsum-1line-comments.docx") as source,
        zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as target,
    ):
        for name in source.namelist():
            data = source.read(name)
            if name == "word/document.xml":
                data = data.replace(b"Lorem ipsum", b"4893013335386137 Lorem ipsum", 1)
            target.writestr(name, data)
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(part)
    raw = bytearray(path.read_bytes())
    # Past the local header's fixed 30 bytes, the name, and the start of the XML.
    raw[info.header_offset + 30 + len(part) + 100] ^= 0xFF
    path.write_bytes(bytes(raw))
    return path


@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "part, reason",
    [
        ("word/comments.xml", "Word comments could not be read"),
        ("docProps/core.xml", "Word document properties could not be read"),
    ],
)
def test_docx_bad_crc_in_an_optional_part_keeps_the_body(tmp_path: Path, part: str, reason: str) -> None:
    """BadZipFile from an optional part must not reach the handler's silent BadZipFile return."""
    item = FilesystemItem(_docx_with_bad_crc(tmp_path, part))

    content = " ".join(DocxHandler().read(item, Config()))

    assert "4893013335386137" in content
    assert item.unread_reason == reason


@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "part, closing_tag, reason",
    [
        ("word/comments.xml", rb"</w:comments>", "Word comments could not be read"),
        ("docProps/core.xml", rb"</cp:coreProperties>", "Word document properties could not be read"),
    ],
)
def test_docx_malformed_xml_in_an_optional_part_keeps_the_body(
    tmp_path: Path, part: str, closing_tag: bytes, reason: str
) -> None:
    path = _docx_with_rewritten_parts(
        tmp_path,
        {
            part: (closing_tag, b""),
            "word/document.xml": (rb"Lorem ipsum", b"4893013335386137 Lorem ipsum"),
        },
    )
    item = FilesystemItem(path)

    content = " ".join(DocxHandler().read(item, Config()))

    assert "4893013335386137" in content
    assert item.unread_reason == reason


_NOTES_FIXTURE = "testdata/docx/lorem-ipsum-1line-with-footnote-endnote.docx"


@pytest.mark.filehandlers
@pytest.mark.parametrize(
    "fixture, part, name, kept",
    [
        ("testdata/docx/lorem-ipsum-1line-header-footer.docx", "word/header1.xml", "header", "Footer text"),
        ("testdata/docx/lorem-ipsum-1line-header-footer.docx", "word/footer1.xml", "footer", "Header Text"),
        (_NOTES_FIXTURE, "word/footnotes.xml", "footnotes", "ENDNOTE"),
        (_NOTES_FIXTURE, "word/endnotes.xml", "endnotes", "FOOTNOTE"),
    ],
)
def test_docx_bad_text_part_keeps_the_rest(fixture: str, part: str, name: str, kept: str, tmp_path: Path) -> None:
    """A bad CRC or malformed XML in one part used to lose the body, silently or with an error."""
    path = tmp_path / "bad-part.docx"
    with zipfile.ZipFile(fixture) as source, zipfile.ZipFile(path, "w") as target:
        for item_name in source.namelist():
            data = source.read(item_name)
            target.writestr(item_name, data[: len(data) // 2] if item_name == part else data)
    item = FilesystemItem(path)

    content = " ".join(DocxHandler().read(item, Config()))

    assert "Lorem ipsum" in content
    assert kept in content
    assert item.unread_reason == f"Word {name} could not be read"


@pytest.mark.filehandlers
def test_docx_bad_body_keeps_the_notes_and_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "bad-body.docx"
    with zipfile.ZipFile(_NOTES_FIXTURE) as source, zipfile.ZipFile(path, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, data[: len(data) // 2] if name == "word/document.xml" else data)
    item = FilesystemItem(path)

    content = " ".join(DocxHandler().read(item, Config()))

    assert "FOOTNOTE" in content
    assert "body" in (item.unread_reason or "")


@pytest.mark.filehandlers
def test_docx_not_a_zip_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "fake.docx"
    path.write_bytes(b"this is not a zip file")
    item = FilesystemItem(path)

    assert list(DocxHandler().read(item, Config())) == []
    assert item.unread_reason == "Word document could not be read"
    assert item.nothing_read


@pytest.mark.filehandlers
def test_docx_damaged_rels_is_reported(tmp_path: Path) -> None:
    """docx2python needs every .rels file to read any part, so this one costs the whole document."""
    path = tmp_path / "bad-rels.docx"
    with zipfile.ZipFile(_NOTES_FIXTURE) as source, zipfile.ZipFile(path, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, data[: len(data) // 2] if name == "word/_rels/document.xml.rels" else data)
    item = FilesystemItem(path)

    assert list(DocxHandler().read(item, Config())) == []
    assert item.unread_reason == "Word document could not be read"


@pytest.mark.filehandlers
def test_docx_zero_bytes_is_not_reported() -> None:
    item = FilesystemItem(Path("testdata/docx/empty-file.docx"))

    assert list(DocxHandler().read(item, Config())) == []
    assert item.unread_reason is None


@pytest.mark.filehandlers
def test_docx_access_denied_still_reaches_the_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An OSError is not damage to the document, and the coordinator reports access denied differently."""
    import docx2python.docx_reader as reader_mod

    path = tmp_path / "locked.docx"
    path.write_bytes(Path(_NOTES_FIXTURE).read_bytes())

    def denied(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(reader_mod.zipfile, "ZipFile", denied)

    with pytest.raises(PermissionError):
        list(DocxHandler().read(FilesystemItem(path), Config()))


@pytest.mark.filehandlers
def test_docx_with_only_its_body_damaged_and_no_other_text_read_nothing(tmp_path: Path) -> None:
    """Empty headers and notes read without error, but no text was read, only the document properties."""
    path = tmp_path / "bad-body.docx"
    with zipfile.ZipFile("testdata/docx/lorem-ipsum-1line.docx") as source, zipfile.ZipFile(path, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, data[: len(data) // 2] if name == "word/document.xml" else data)
    item = FilesystemItem(path)

    list(DocxHandler().read(item, Config()))

    assert item.nothing_read


@pytest.mark.filehandlers
def test_docx_with_a_bad_footnote_still_read_text(tmp_path: Path) -> None:
    path = tmp_path / "bad-note.docx"
    with zipfile.ZipFile(_NOTES_FIXTURE) as source, zipfile.ZipFile(path, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, data[: len(data) // 2] if name == "word/footnotes.xml" else data)
    item = FilesystemItem(path)

    list(DocxHandler().read(item, Config()))

    assert item.unread_reason
    assert not item.nothing_read


@pytest.mark.filehandlers
def test_docx_comment_range_mismatch_is_reported_and_keeps_the_body(tmp_path: Path) -> None:
    """docx2python warns and returns no comments, which used to look fully read."""
    path = _docx_with_rewritten_parts(
        tmp_path,
        {
            "word/comments.xml": (rb"</w:comments>", b'<w:comment w:id="99"/></w:comments>'),
        },
    )
    item = FilesystemItem(path)

    content = " ".join(DocxHandler().read(item, Config()))

    assert "Lorem ipsum" in content
    assert item.unread_reason == "Word comments could not be read"
    assert not item.nothing_read


@pytest.mark.filehandlers
def test_docx_other_warnings_are_not_turned_into_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from docx2python.docx_output import DocxContent

    def warns(_self: object) -> list[tuple[str, str, str, str]]:
        warnings.warn("something else", UserWarning, stacklevel=2)
        return []

    monkeypatch.setattr(DocxContent, "comments", property(warns))
    item = FilesystemItem(Path("testdata/docx/lorem-ipsum-1line-comments.docx"))

    with pytest.warns(UserWarning, match="something else"):
        list(DocxHandler().read(item, Config()))

    assert item.unread_reason is None
