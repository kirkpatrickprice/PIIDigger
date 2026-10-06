import pytest

from piidigger.orchestration.sources import FilesystemItem
from piidigger.protocols import ScannableItem


@pytest.mark.unit
class TestFilesystemItem:
    def test_satisfies_protocol(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")
        item = FilesystemItem(f)
        assert isinstance(item, ScannableItem)

    def test_display_path(self, tmp_path):
        f = tmp_path / "sample.txt"
        f.write_bytes(b"x")
        item = FilesystemItem(f)
        assert item.display_path == str(f)

    def test_ext(self, tmp_path):
        f = tmp_path / "report.csv"
        f.write_bytes(b"a,b")
        assert FilesystemItem(f).ext == ".csv"

    def test_ext_no_suffix(self, tmp_path):
        f = tmp_path / "Makefile"
        f.write_bytes(b"all:")
        assert FilesystemItem(f).ext == ""

    def test_mime_injected(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00")
        item = FilesystemItem(f, mime="application/octet-stream")
        assert item.mime == "application/octet-stream"

    def test_mime_defaults_none(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_bytes(b"hi")
        assert FilesystemItem(f).mime is None

    def test_size(self, tmp_path):
        f = tmp_path / "content.txt"
        f.write_bytes(b"hello world")
        assert FilesystemItem(f).size == 11

    def test_depth_always_zero(self, tmp_path):
        f = tmp_path / "nested" / "deep.txt"
        f.parent.mkdir()
        f.write_bytes(b"x")
        assert FilesystemItem(f).depth == 0

    def test_open_stream_reads_bytes(self, tmp_path):
        f = tmp_path / "binary.dat"
        f.write_bytes(b"\xde\xad\xbe\xef")
        item = FilesystemItem(f)
        stream = item.open_stream()
        try:
            data = stream.read()
        finally:
            stream.close()
        assert data == b"\xde\xad\xbe\xef"

    def test_materialize_returns_same_path(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_bytes(b"x")
        item = FilesystemItem(f)
        assert item.materialize() == f
