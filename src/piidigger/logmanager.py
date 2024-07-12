import logging
import multiprocessing as mp
from logging.handlers import QueueHandler
from time import sleep

from piidigger import (
    console,
    queuefuncs,)
from piidigger.globalvars import SENTINEL

class LogManager:
    def __init__(self,
                 logFile: str,
                 logLevel: str,
                 logQueue: mp.Queue,) -> None:
        
        self.logFile=logFile
        self.logLevel=logLevel
        self.logQueue=logQueue

    def logProcessor(self, stopEvent: mp.Event):
        """
        Processes log events from a queue and writes them to a log file.
        """
        try:
            logger = logging.getLogger('logProcessor')

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

                message = queuefuncs.getItem(self.logQueue)

                if message == SENTINEL:
                    stopCause = 'endQueue'
                    break

                if message is None:
                    continue

                logger.handle(message)
        except KeyboardInterrupt:
            console.normal('\n')
            console.warn('User terminated scan.  Shutting down.')

            # Give other processes a chance to write their final messages to the queue
            sleep(2)
            queuefuncs.clearQ(self.logQueue)
            stopCause='ctrlc'
        finally:
            logger.info('Stopping logProcessor [%s] (PID=%d)', str(stopCause), mp.current_process().pid)

    def getLogger(self,
                  name: str = '',) -> logging.Logger:
        """
        Returns a logger instance for subprocesses to use, which puts log events onto a queue.
        """
        logger = logging.getLogger(name)
        logger.setLevel(self.logLevel)
        if not logger.handlers:
            logger.addHandler(QueueHandler(self.logQueue))
        logger.propagate = False

        return logger