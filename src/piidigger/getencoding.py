from charset_normalizer import from_bytes


def detect_encoding(data: bytes) -> str | None:
    """Detect the character encoding of raw bytes.

    Uses charset-normalizer's from_bytes() so no file path is required —
    works with data from any source (filesystem, archive member, etc.).

    Returns the best-guess encoding name, or None if the data is empty or
    the encoding cannot be determined.
    """
    if not data:
        return None
    best = from_bytes(data).best()
    return best.encoding if best is not None else None
