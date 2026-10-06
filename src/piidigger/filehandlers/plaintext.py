from collections.abc import Iterator

from piidigger.exceptions import UndetectableEncodingError
from piidigger.filehandlers._sharedfuncs import ContentBuffer
from piidigger.getencoding import detect_file_encoding
from piidigger.models.config import Config

HANDLES = {
    "ext": [
        ".aplt",
        ".applescript",
        ".armx",
        ".asp",
        ".asax",
        ".asmx",
        ".aspx",
        ".bat",
        ".c",
        ".cc",
        ".cfm",
        ".clj",
        ".cljs",
        ".clojure",
        ".cob",
        ".cpp",
        ".csh",
        ".csv",
        ".erl",
        ".h",
        ".hrl",
        ".htm",
        ".ht4",
        ".html",
        ".html5",
        ".go",
        ".gvy",
        ".j",
        ".json",
        ".js",
        ".jsp",
        ".log",
        ".perl",
        ".php",
        ".pl",
        ".ps1",
        ".py",
        ".rb",
        ".scpt",
        ".sdef",
        ".ser",
        ".sh",
        ".toml",
        ".txt",
        ".vb",
        ".xml",
        ".yaml",
        ".zsh",
    ],
    "mime": [
        "application/json",
        "application/toml",
        "application/xml",
        "text/html",
        "text/plain",
    ],
}

# Backward-compat alias for globalfuncs dynamic discovery (uses lowercase 'handles')
handles = HANDLES


# Upper bound on the characters readline() returns in one call.  A longer line
# arrives in pieces over several calls (nothing is skipped), so a file with no
# newlines, e.g. minified JSON, never lands in memory whole.
#
# There is a risk that if a PII record spans the boundary, it will
# be split across multiple reads.  This would lead a false negative in PII detection.
_MAX_LINE_CHARS = 1024 * 1024


class PlaintextHandler:
    """FileHandler for text-based files.

    Reads the file line by line from source.materialize(), so memory stays
    bounded whatever the file size.  Archive members are extracted to disk
    before scanning, so a real path is always available.

    The encoding comes from detect_file_encoding(), which samples the start of
    the file — the same answer `piidigger inspect encoding` reports.  Reading
    stops once config.plaintext.max_scan_bytes of text has been read.

    Raises UndetectableEncodingError for a non-empty file whose encoding
    cannot be detected.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        path = source.materialize()
        enc = detect_file_encoding(path)
        if not enc:
            # An empty file has nothing to read.  Anything else was never read,
            # and the caller needs to say so.
            if path.stat().st_size == 0:
                return
            raise UndetectableEncodingError("text encoding could not be detected")

        content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)
        max_scan_chars = config.plaintext.max_scan_bytes
        chars_read = 0
        with open(path, encoding=enc, errors="replace") as f:
            while line := f.readline(_MAX_LINE_CHARS):
                content_buffer.append_content(line)
                if content_buffer.content_buffer_full():
                    yield content_buffer.get_content()
                chars_read += len(line)
                if chars_read >= max_scan_chars:
                    break

        final = content_buffer.finalize_content()
        if final:
            yield final


handler = PlaintextHandler()
