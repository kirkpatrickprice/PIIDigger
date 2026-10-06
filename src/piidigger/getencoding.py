from pathlib import Path

from charset_normalizer import from_bytes

# Bytes read from the start of a file to guess its encoding.  charset-normalizer
# decodes its whole input internally, so feeding it a whole multi-GB file costs
# several times the file size in memory.  4 MiB is large enough that binary junk
# at the start (a 64 KiB header made a 256 KiB sample undetectable) is outweighed
# by the text after it, yet small enough that junk MBs into the file cannot pull
# the guess away from UTF-8.  It costs ~12 MiB and ~0.01 s.  See
# testdata/plaintext/json-logs/compare_sample_sizes.py.
_ENCODING_SAMPLE_BYTES: int = 4 * 1024 * 1024


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


def detect_file_encoding(path: Path) -> str | None:
    """Return the encoding PIIDigger uses to read the text file at path.

    The single source of truth for both the plaintext file handler and
    `piidigger inspect encoding`, so the two can never disagree.

    Only the first _ENCODING_SAMPLE_BYTES are examined.  Two adjustments make
    that sample safe to guess from:

    * A UTF-8 character split by the sample boundary is trimmed off, since a
      partial character can make charset-normalizer misjudge the whole sample
      (e.g. as utf_16_be or cp932).
    * An "ascii" guess is returned as "utf_8".  A pure-ASCII prefix says
      nothing about the rest of the file, and UTF-8 decodes ASCII identically
      while still handling any non-ASCII text further in.

    Returns None if the file is empty or the encoding cannot be determined.
    OSError from opening or reading the file is left to the caller.
    """
    with open(path, "rb") as f:
        sample = f.read(_ENCODING_SAMPLE_BYTES)
    # A shorter read is the whole file, so no character was cut off.
    if len(sample) == _ENCODING_SAMPLE_BYTES:
        sample = _trim_partial_utf8(sample)
    enc = detect_encoding(sample)
    return "utf_8" if enc == "ascii" else enc


def _trim_partial_utf8(sample: bytes) -> bytes:
    """Drop an incomplete UTF-8 character from the end of sample.

    Walks back over up to 3 trailing continuation bytes (0b10xxxxxx) to the
    character's lead byte (0xC2-0xF4), whose high bits give the character's
    length.  If fewer bytes than that follow it, the character was cut off and
    is removed.  Complete characters and non-UTF-8 tails are left unchanged —
    including a UTF-16 byte-order mark (FF FE / FE FF), since 0xFE and 0xFF
    never occur in UTF-8.
    """
    lead = len(sample) - 1
    while lead >= 0 and len(sample) - lead <= 3 and (sample[lead] & 0xC0) == 0x80:
        lead -= 1
    if lead < 0 or not 0xC2 <= sample[lead] <= 0xF4:
        return sample
    needed = 2 if sample[lead] < 0xE0 else 3 if sample[lead] < 0xF0 else 4
    return sample[:lead] if len(sample) - lead < needed else sample
