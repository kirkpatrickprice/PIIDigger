from logging import INFO

from chardet import UniversalDetector

from piidigger.logmanager import LogManager

def getEncoding(filename: str,
                logManager: LogManager,) -> str:
    '''
    Uses chardet to indenty the file encoding by reading MAXLINES of data.

    '''

    logger = logManager.getLogger('getEncoding')
    detector = UniversalDetector()
    detector.logger.level=INFO
        
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