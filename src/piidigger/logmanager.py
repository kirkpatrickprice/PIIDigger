import logging
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
            logger=logging.getLogger()

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

    def get_logger(self, name: str, ) -> logging.Logger:
        """
        Returns a logger instance for subprocesses to use, which puts log events onto a queue.
        """
        logger = logging.getLogger(name)
        logger.setLevel(self.logLevel)
        logger.addHandler(logging.handlers.QueueHandler(self.logQueue))
        logger.propagate = False
        return logger
