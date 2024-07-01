from multiprocessing import Queue
from queue import Empty
from time import sleep

def clearQ(q: Queue):
    '''Clears a queue of all contents'''
    while True:
        try:
            _=q.get(block=False)
        except Empty:
            break
        except KeyboardInterrupt:
            continue 
    

def waitOnQ(q: Queue):
    while not q.empty():
        sleep(.01)