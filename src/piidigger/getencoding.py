import os

from charset_normalizer import from_path

from piidigger.logmanager import LogManager


def getEncoding(filename: str,
                logManager: LogManager,) -> str | None:
    '''
    Uses charset-normalizer to identify the file encoding.

    Returns the detected encoding name as a string, or None if the encoding
    cannot be determined (e.g. binary content or an unreadable file).  The
    None contract is preserved so existing file handlers can keep their
    "unknown encoding -> skip" behaviour.
    '''

    logger = logManager.getLogger('getEncoding')

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
