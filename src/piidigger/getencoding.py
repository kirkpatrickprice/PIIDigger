import os

from charset_normalizer import from_bytes, from_path

from piidigger.logmanager import LogManager


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


def get_encoding(filename: str,
                 log_manager: LogManager,) -> str | None:
    '''
    Uses charset-normalizer to identify the file encoding.

    Returns the detected encoding name as a string, or None if the encoding
    cannot be determined (e.g. binary content or an unreadable file).  The
    None contract is preserved so existing file handlers can keep their
    "unknown encoding -> skip" behaviour.
    '''

    logger = log_manager.getLogger('getEncoding')

    try:
        # An empty file has no encoding to detect.  chardet reported None here;
        # charset-normalizer would guess utf-8, so guard to preserve the
        # "unknown encoding -> skip" contract that file handlers rely on.
        if os.path.getsize(filename) == 0:
            guess = None
        else:
            best = from_path(filename).best()
            guess = best.encoding if best is not None else None
    except Exception as e:
        logger.info('%s: %s', filename, str(e))
        # Mimic the previous detector output to preserve code integrity for function consumers
        guess = None

    logger.debug('Filename %s charset-normalizer results: %s', filename, str(guess))

    return guess
