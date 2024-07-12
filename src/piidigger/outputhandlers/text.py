import multiprocessing as mp

import yaml

from piidigger import console
from piidigger import queuefuncs
from piidigger.globalvars import SENTINEL
from piidigger.logmanager import LogManager

def processResult(outFilename: str,
                  queue: mp.Queue,
                  stopEvent: mp.Event,
                  logManager: LogManager,):
    
    
    try:
        logger = logManager.getLogger('yaml_handler')
        logger.info('Starting YAML output processor (%s)', mp.current_process().pid)
        with open(outFilename, 'w', encoding="utf-8") as of:
            while True:
                if stopEvent.is_set():
                    break
                item = queuefuncs.getItem(queue)
                if item == SENTINEL:
                    break
                if item ==  None:
                    continue
                yaml.dump(item, of, indent=4)
    except KeyboardInterrupt:
        pass
    except PermissionError as e:
        console.error(str(e))
        stopEvent.set()
    finally:
        logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)