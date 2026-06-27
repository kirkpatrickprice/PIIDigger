from collections.abc import Iterator

from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.getencoding import detect_encoding, get_encoding
from piidigger.globalvars import DEFAULT_CHUNK_COUNT, MAX_CHUNK_SIZE
from piidigger.logmanager import LogManager

HANDLES = {
    'ext': [
        '.aplt', '.applescript', '.armx', '.asp', '.asax', '.asmx', '.aspx',
        '.bat',
        '.c', '.cc', '.cfm', '.clj', '.cljs', '.clojure', '.cob', '.cpp', '.csh', '.csv',
        '.erl',
        '.h', '.hrl', '.htm', '.ht4', '.html', '.html5',
        '.go', '.gvy',
        '.j', '.json', '.js', '.jsp',
        '.log',
        '.perl', '.php', '.pl', '.ps1', '.py',
        '.rb',
        '.scpt', '.sdef', '.ser', '.sh',
        '.toml', '.txt',
        '.vb',
        '.xml',
        '.yaml',
        '.zsh',
    ],
    'mime': [
        'application/json',
        'application/toml',
        'application/xml',
        'text/html',
        'text/plain',
    ],
}


def read_file(filename: str,
              log_manager: LogManager,
              max_chunk_count: int = DEFAULT_CHUNK_COUNT,
              ) -> Iterator[str]:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a generator object tied to MAX_CHUNK_SIZE * max_chunk_count bytes of text.
    "filename" is a string of the path and filename to process.
    '''

    logger = log_manager.getLogger('plaintext_handler')

    enc = get_encoding(filename=filename, log_manager=log_manager)

    if enc is None:
        logger.info('%s: Unknown encoding type', filename)
        return ['']
    else:
        logger.debug('%s: Encoding %s', filename, enc)

    # After getting the encoding from chardet, replace any unexpected characters with a plain ASCII "?"
    # The risk is we could lose something important, but if that's the one piece of data anywhere on the file system that would have matched,
    # then it's a risk worth taking for a more stable discovery tool.  More likely is that we might miss ONE INSTANCE of data in a file system that has
    # many more instances for discovery.

    # File IO is the bottle neck but we could also hit some really big files.
    # By returning (through yield) the content in chunks, we can strike a balance between memory consumption and file IO speed.

    # First we open the file, then we add each line so long as the resulting line length remains less than max_content_size.
    # For the last line, we add one word at a time until we reach the limit.

    try:
        with open(filename, encoding=enc, errors='replace') as f:
            handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * max_chunk_count)
            for line in f:
                handler.append_content(line)
                if handler.content_buffer_full():
                    yield handler.get_content()

    # Once we've processed the entire file, it's time to send that last bit of info that hasn't already been sent.
        logger.debug('%s: Read %d lines', filename, handler.total_bytes)

        # Return the last chunk of content
        yield handler.finalize_content()

    except FileNotFoundError:
        logger.error('Previously discovered file no longer exists: %s. File skipped', f.absolute())
    except PermissionError as e:
        logger.error('PermissionError adding %s.  File skipped.  Error message: %s', f.absolute(), str(e))
    except OSError as e:
        logger.error('OSError adding %s.  File skipped.  Error message: %s', f.absolute(), str(e))
    except UnicodeDecodeError as e:
        logger.error('Unicode error processing file %s (enc=%s): %s', filename, enc, e)
    except LookupError:
        logger.error('Codec lookup error processing file %s (enc=%s)', filename, enc)
    except Exception as e:
        logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))


# ---------------------------------------------------------------------------
# 2.0 FileHandler protocol implementation
# ---------------------------------------------------------------------------

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

        text = raw.decode(enc, errors='replace')
        chunk_handler: ContentHandler = ContentHandler(max_content_size=MAX_CHUNK_SIZE * DEFAULT_CHUNK_COUNT)
        for line in text.splitlines():
            chunk_handler.append_content(line)
            if chunk_handler.content_buffer_full():
                yield chunk_handler.get_content()

        final = chunk_handler.finalize_content()
        if final:
            yield final


handler = PlaintextHandler()
