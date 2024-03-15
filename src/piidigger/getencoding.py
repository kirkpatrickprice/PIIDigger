import logging
import os
import sys

from chardet import UniversalDetector

moduleName='getencoding'

logger=logging.getLogger(moduleName)

def getEncoding(filename: str,) -> str:
    '''
    Uses chardet to indenty the file encoding by reading MAXLINES of data.

    '''

# Determing the encoding is not an exact science.  In this method, we read up to MAXLINES of data for each encoding type and if no error is thrown, then that's our guess.
# If we do throw a UnicodeDecodeError exception, then we try the next one.

# Encodings should be listed in order of preference.  For instance, try UTF-8 before trying the more generic ASCII.  
# Taken from https://docs.python.org/3.9/library/codecs.html#standard-encodings and includes the ones that support Western and many European languages
    logger=logging.getLogger(moduleName)
    detector = UniversalDetector()
    detector.logger.level=logging.INFO
        
    try:
        with open(filename, 'rb') as f:
            for line in f.readlines():
                detector.feed(line)
                if detector.done: 
                    break
        detector.close()
        guess=detector.result['encoding']
    except Exception as e:
        logger.info('%s: %s', filename, str(e))
        # Mimic the chardet.detector output to preserve code integrety for function consumers
        guess=None
    
    logger.debug('Filename %s chardet results: %s', filename, str(guess))

    return guess

def main():
    args=sys.argv[1:]
    for arg in args:
        if os.path.exists(arg):
            print('Filename: %s\nEncoding: %s\n' % (arg, getEncoding(arg)))
        else:
            print('Filename not found: %s\n' % arg)


if __name__ == "__main__":

    main()