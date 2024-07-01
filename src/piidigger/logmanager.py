import logging
from logging.handlers import QueueHandler
import multiprocessing as mp
from time import sleep

from piidigger import (
    console,
    globalfuncs
)

class LogManager:
    def __init__(self, logFile: str, logLevel: str, logQueue: mp.Queue):
        self.logFile = logFile
        self.logLevel = logLevel
        self.logQueue = logQueue

    def logProcessor(self, stopEvent: mp.Event):
        """
        Processes log events from a queue and writes them to a log file.
        """
        try:
            logger=logging.getLogger('logProcessor')

            logFileFormatter=logging.Formatter('%(asctime)s:[%(name)s]:%(levelname)s:%(message)s')
            logFileHandler=logging.FileHandler(filename=self.logFile,mode='w',encoding='utf-8')
            logFileHandler.setFormatter(logFileFormatter)

            logger.setLevel(self.logLevel)
            logger.addHandler(logFileHandler)

            logger.info('Starting logProcessor (%s)', mp.current_process().pid)
            stopCause = None

            while True:
                if stopEvent.is_set():
                    stopCause = 'stopEvent'
                    break

                message = self.logQueue.get(1)

                if message == None:
                    stopCause = 'endQueue'
                    break

                logger.handle(message)
        except KeyboardInterrupt:
            console.normal('\n')
            console.warn('User terminated scan.  Shutting down.')

            # Give other processes a chance to write their final messages to the queue
            sleep(2)
            globalfuncs.clearQ(self.logQueue)
            stopCause='ctrlc'
        finally:
            logger.info('[logProcessor]Stopping logProcessor (%s)', str(stopCause))

    @staticmethod
    def getLogger( name: str = '',
                    logConfig: dict={'q': mp.Queue, 
                                    'level': None},
                  ) -> logging.Logger:
        """
        Returns a logger instance for subprocesses to use, which puts log events onto a queue.
        """
        logger = logging.getLogger(name)
        logger.setLevel(logConfig['level'])
        if not logger.handlers:
            logger.addHandler(QueueHandler(logConfig['q']))
        logger.propagate = False
        return logger
