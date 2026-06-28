from pathlib import Path

import pytest

from piidigger.filehandlers.docx import DocxHandler
from piidigger.orchestration.sources import FilesystemItem


def _read(path: Path) -> list[str]:
    return list(DocxHandler().read(FilesystemItem(path)))


@pytest.mark.filehandlers
def test_docx_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        _read(Path("testdata/docx/does-not-exist.docx"))


@pytest.mark.filehandlers
def test_docx_empty_file() -> None:
    chunks = _read(Path("testdata/docx/empty-file.docx"))
    assert chunks == []


# All non-empty DOCX fixtures fit in a single chunk with DEFAULT_CHUNK_COUNT.
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
    # With DEFAULT_CHUNK_COUNT both paragraphs arrive as one large chunk.
    chunks = _read(Path("testdata/docx/lorem-ipsum-2paragraph.docx"))
    content = " ".join(chunks)
    assert "Lorem ipsum dolor sit amet" in content
    assert "Iaculis at erat pellentesque adipiscing" in content
    assert "Randy Bartels" in content
