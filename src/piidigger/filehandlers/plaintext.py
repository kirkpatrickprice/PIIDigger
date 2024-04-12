import codecs, logging
from logging.handlers import QueueHandler
from collections.abc import Iterator

from piidigger.getencoding import getEncoding
from piidigger.filehandlers._sharedfuncs import ContentHandler
from piidigger.globalvars import (
    maxChunkSize,
    defaultChunkCount,
    )

# Each filehandler must have the following:
#   "handles" -     dictionary to identify lists of file extensions and mime types that the handler will manage.
#                   This will be read by globals upon initial load to build the full list of supported mime types and file extensions
#   "processFile" - Function that manages opening and reading of the file.  The main module will call this handler wtih the "processFile(filename)" function.
#                   processFile should provide the lines of text to each of the dataHandlers

handles={
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


def readFile(filename: str, 
            logConfig: dict,
            maxChunkCount: int = defaultChunkCount,
            ) -> Iterator[str]:
    ''''
    Handle all file IO and text extraction operations for this file type.  Returns a generator object tied to maxChunkSize (650) * maxChunkCount bytes of text.  
    "filename" is a string of the path and filename to process.  logConfig is a two-key dictionary consisting of 'q' and 'level' for logging.
    '''

    logger = logging.getLogger('plaintext_handler')
    if not logger.handlers:
        logger.addHandler(QueueHandler(logConfig['q']))
    logger.setLevel(logConfig['level'])
    logger.propagate=False
    
    
    enc = getEncoding(filename)

    if enc == None:
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

    # First we open the file, then we add each line so long as the resulting line length remains less than maxContentSize.  
    # For the last line, we add one word at a time until we reach the limit.  

    try:
        with codecs.open(filename, 'r', encoding=enc, errors='replace') as f:
            handler: ContentHandler = ContentHandler(maxContentSize = maxChunkSize * maxChunkCount)
            for line in f:
                handler.appendContent(line)
                if handler.contentBufferFull():
                    yield handler.getContent()

    # Once we've processed the entire file, it's time to send that last bit of info that hasn't already been sent.
        logger.debug('%s: Read %d lines', filename, handler.totalBytes)

        # Return the last chunk of content    
        yield handler.finalizeContent()

    except FileNotFoundError:
        logger.error('Previously discovered file no longer exists: %s. File skipped', f.absolute())
    except PermissionError as e:
        logger.error('PermissionError adding %s.  File skipped.  Error message: %s', f.absolute(), str(e))
    except OSError as e:
        logger.error('OSError adding %s.  File skipped.  Error message: %s', f.absolute(), str(e))
    except UnicodeDecodeError as e:
        logger.error('Unicode error processing file %s (enc=%s): %s', filename, enc, e)
    except LookupError as e:
        logger.error('Codec lookup error processing file %s (enc=%s)', filename, enc)
    except Exception as e:
        logger.error('Unknown exception on file %s.  File skipped.  Error message: %s', filename, str(e))

