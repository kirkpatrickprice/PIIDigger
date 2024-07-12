from multiprocessing import Queue
from queue import Empty
from time import sleep

TIMEOUT=.1

def clearQ(q: Queue):
    '''Clears a queue of all contents'''
    while True:
        try:
            _ = q.get(block=False)
        except Empty:
            break
        except KeyboardInterrupt:
            continue 
    
def getItem(q: Queue):
    '''Returns the next item in the queue.  Uses a timeout to prevent blocking indefinitely'''
    try:
        return q.get(timeout=TIMEOUT)
    except Empty:
        return None

def waitOnQ(q: Queue):
    while not q.empty():
        sleep(TIMEOUT)