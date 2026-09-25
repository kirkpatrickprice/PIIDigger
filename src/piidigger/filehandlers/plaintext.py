from collections.abc import Iterator

from piidigger.filehandlers._sharedfuncs import ContentBuffer
from piidigger.getencoding import detect_encoding
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


class PlaintextHandler:
    """FileHandler for text-based files.

    Reads via source.open_stream() — works for both on-disk files and
    archive members without requiring a real filesystem path.
    Encoding is detected from the raw bytes via charset-normalizer.
    """

    def read(self, source, config: Config) -> Iterator[str]:  # source: ScannableItem
        stream = source.open_stream()
        try:
            raw = stream.read()
        finally:
            stream.close()

        enc = detect_encoding(raw)
        if not enc:
            return

        text = raw.decode(enc, errors="replace")
        content_buffer: ContentBuffer = ContentBuffer(max_bytes=config.buffer.max_buffer_bytes)
        for line in text.splitlines():
            content_buffer.append_content(line)
            if content_buffer.content_buffer_full():
                yield content_buffer.get_content()

        final = content_buffer.finalize_content()
        if final:
            yield final


handler = PlaintextHandler()
