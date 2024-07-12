import multiprocessing as mp
import json

from piidigger import console
from piidigger import queuefuncs
from piidigger.globalvars import SENTINEL
from piidigger.logmanager import LogManager

def processResult(outFilename: str,
                  queue: mp.Queue,
                  stopEvent: mp.Event,
                  logManager: LogManager,
                 ):
    
    
    # For JSON output, we need to store all results in a list and write them once the queue is shutdown
    try:
        logger = logManager.getLogger('json_handler')
        logger.info('Starting JSON output processor (%s)', mp.current_process().pid)
        allResults=list()

        while True:
            if stopEvent.is_set():
                break
            item = queuefuncs.getItem(queue)
            if item == SENTINEL:
                break
            if item ==  None:
                continue
            allResults.append(item)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            with open(outFilename, 'w', encoding='utf-8') as of:
                json.dump(allResults, of, indent=4)
            logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)
        except PermissionError as e:
            console.error(str(e))
            stopEvent.set()