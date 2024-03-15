import multiprocessing as mp

import yaml

from piidigger import console

def processResult(outFilename: str,
                  queue: mp.Queue,
                  stopEvent: mp.Event,
                 ):
    
    
    try:
        with open(outFilename, 'w', encoding="utf-8") as of:
            while True:
                if stopEvent.is_set():
                    break
                result = queue.get()
                if result == None:
                    break
                yaml.dump(result, of, indent=4)
    except KeyboardInterrupt:
        pass
    except PermissionError as e:
        console.error(str(e))
        stopEvent.set()