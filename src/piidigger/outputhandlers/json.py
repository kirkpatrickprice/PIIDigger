import multiprocessing as mp
import json

from piidigger import console

def processResult(outFilename: str,
                  queue: mp.Queue,
                  stopEvent: mp.Event,
                 ):
    
    
    # For JSON output, we need to store all results in a list and write them once the queue is shutdown
    try:
        allResults=list()

        while True:
            if stopEvent.is_set():
                break
            item=queue.get()
            if item == None:
                break
            allResults.append(item)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            with open(outFilename, 'w', encoding='utf-8') as of:
                json.dump(allResults, of, indent=4)
        except PermissionError as e:
            console.error(str(e))
            stopEvent.set()