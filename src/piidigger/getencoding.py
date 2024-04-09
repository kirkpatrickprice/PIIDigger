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