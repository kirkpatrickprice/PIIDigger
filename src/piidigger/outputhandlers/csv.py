import multiprocessing as mp

import csv

from piidigger import console
from piidigger import queuefuncs
from piidigger.globalvars import SENTINEL
from piidigger.logmanager import LogManager

def processResult(outFilename: str,
                  queue: mp.Queue,
                  stopEvent: mp.Event,
                  logManager: LogManager,):
    
    
    try:
        logger = logManager.getLogger('csv_handler')
        logger.info('Starting CSV output processor (%s)', mp.current_process().pid)
        # Open the output file for writing
        with open(outFilename, 'w', newline='', encoding='utf-8') as of:
            # Create a CSV writer object
            field_names = ['filename', 'datatype', 'value',]
            writer = csv.DictWriter(of, quoting=csv.QUOTE_MINIMAL, fieldnames=field_names)
            writer.writeheader()
            while True:
                if stopEvent.is_set():
                    break
                item = queuefuncs.getItem(queue)
                if item == SENTINEL:
                    break
                if item ==  None:
                    continue
                filename = item['filename']
                flattened_list: list[dict] = flatten_matches(matches = item['matches'], filename=filename,)
                for flattened_item in flattened_list:
                    writer.writerow(flattened_item)
    except KeyboardInterrupt:
        pass
    except PermissionError as e:
        console.error(str(e))
        stopEvent.set()
    finally:
        logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)

def flatten_matches(matches: dict, filename: str) -> list[dict]:
    items = []
    for key, value in matches.items():
        datatype = key
        if isinstance(value, dict):
            items.extend(flatten_value(dict = value, filename = filename, datatype = datatype))
        elif isinstance(value, list):
            for item in value:
                items.append({'filename': filename, 'datatype': datatype, 'value': item})
        else:
            raise TypeError(f"Unsupported type: {type(value)} in {value}")
    return items

def flatten_value(dict: dict, filename: str, datatype: str, sep: str = ': ') -> list[dict]:
    items = []
    for key, value in dict.items():
        if isinstance(value, list):
            for val in value:
                items.append({'filename': filename, 'datatype': datatype, 'value': f'{key}{sep}{val}'})
        else:
            items.append({'filename': filename, 'datatype': datatype, 'value': f'{key}{sep}{value}'})
    return items