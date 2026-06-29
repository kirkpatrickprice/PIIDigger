from collections.abc import Iterator

from piidigger.filehandlers._constants import DEFAULT_CHUNK_COUNT, MAX_CHUNK_SIZE
from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.getencoding import detect_encoding

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


class PlaintextHandler:
    """FileHandler for text-based files.

    Reads via source.open_stream() — works for both on-disk files and
    archive members without requiring a real filesystem path.
    Encoding is detected from the raw bytes via charset-normalizer.
    """

    def read(self, source) -> Iterator[str]:  # source: ScannableItem
        stream = source.open_stream()
        try:
            raw = stream.read()
        finally:
            stream.close()

        enc = detect_encoding(raw)
        if not enc:
            return

        text = raw.decode(enc, errors="replace")
        chunk_handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * DEFAULT_CHUNK_COUNT)
        for line in text.splitlines():
            chunk_handler.append_content(line)
            if chunk_handler.content_buffer_full():
                yield chunk_handler.get_content()

        final = chunk_handler.finalize_content()
        if final:
            yield final


handler = PlaintextHandler()
