import multiprocessing as mp

import yaml

from piidigger import console, queuefuncs
from piidigger.globalvars import SENTINEL
from piidigger.logmanager import LogManager


def process_result(out_filename: str,
                   queue: mp.Queue,
                   stop_event: mp.Event,
                   log_manager: LogManager,):

    try:
        logger = log_manager.getLogger('yaml_handler')
        logger.info('Starting YAML output processor (%s)', mp.current_process().pid)
        with open(out_filename, 'w', encoding="utf-8") as of:
            while True:
                if stop_event.is_set():
                    break
                item = queuefuncs.getItem(queue)
                if item == SENTINEL:
                    break
                if item is None:
                    continue
                yaml.dump(item, of, indent=4)
    except KeyboardInterrupt:
        pass
    except PermissionError as e:
        console.error(str(e))
        stop_event.set()
    finally:
        logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)
