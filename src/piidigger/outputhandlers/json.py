import json
import multiprocessing as mp

from piidigger import console, queuefuncs
from piidigger.globalvars import SENTINEL
from piidigger.logmanager import LogManager


def process_result(out_filename: str,
                   queue: mp.Queue,
                   stop_event: mp.Event,
                   log_manager: LogManager,
                   ):

    # For JSON output, we need to store all results in a list and write them once the queue is shutdown
    try:
        logger = log_manager.getLogger('json_handler')
        logger.info('Starting JSON output processor (%s)', mp.current_process().pid)
        all_results = list()

        while True:
            if stop_event.is_set():
                break
            item = queuefuncs.getItem(queue)
            if item == SENTINEL:
                break
            if item is None:
                continue
            all_results.append(item)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            with open(out_filename, 'w', encoding='utf-8') as of:
                json.dump(all_results, of, indent=4)
            logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)
        except PermissionError as e:
            console.error(str(e))
            stop_event.set()
