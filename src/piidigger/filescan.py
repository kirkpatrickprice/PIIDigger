import pathlib
from logging.handlers import QueueHandler
import logging
import sys
import threading
from queue import Queue, Empty
import multiprocessing as mp
    

from piidigger.getmime import getMime
from piidigger import globalfuncs
try:
    from win32api import GetFileAttributes
    win32apiLoaded=True
except ModuleNotFoundError:
    win32apiLoaded=False

from piidigger import classes
from piidigger import console

logger = logging.getLogger(__name__)


def findDirsWorker(config: classes.Config, 
                   queues: dict, 
                   totals: dict,
                   stopEvent: mp.Event,
                  ) -> list:
    '''
    Expects a config object to start from (to get the startDirs paths)

    Populates a queue of all directories that aren't on the excludedDirs list
    '''
    
    
    try:
        ctrlc=False
        logger=logging.getLogger('findDirsWorker')
        logger.addHandler(QueueHandler(queues['logQ']))
        logger.setLevel(config.getLogLevel())
        logger.propagate=False
        
        logger.info('Starting findDirsWorker')

        localQ=list()
        i=0

        for d in config.getStartDirs():
            localQ.append(pathlib.Path(d))
            queues['dirsQ'].put(pathlib.Path(d))
            totals['totalDirs'].value+=1

        while not stopEvent.is_set() or not ctrlc:
            try:
                p=localQ[i]
            except IndexError:
                break
            else:
                i+=1

            
            try:
                for subD in p.iterdir():
                    if stopEvent.is_set():
                        break
                    excludeDir=False
                    try:
                        if subD.is_dir() and not subD.is_symlink():
                            for pattern in config.getExcludeDirs():
                                if str(subD).lower().startswith(pattern.lower()):
                                    logger.debug('Excluding directory %s matched pattern %s', str(subD), pattern)
                                    excludeDir=True
                                    break
                            if not excludeDir:
                                logger.debug('Including directory %s', str(subD))
                                localQ.append(subD)
                                queues['dirsQ'].put(subD)
                                totals['totalDirs'].value+=1
                    except FileNotFoundError:
                        pass
                    except OSError as e:
                        logger.debug('OSError: %s', str(e))
            except PermissionError as e:
                logger.debug('PermissionError: %s', str(e))
            except FileNotFoundError as e:
                logger.debug('FileNotFoundError: %s', str(e))
        
        queues['dirsQ'].put(None)
        globalfuncs.waitOnQ(queues['dirsQ'])
    except KeyboardInterrupt:
        console.normal('\n')
        console.warn('User terminated scan.  Shutting down.')
        logger.info('KeyboardInterrupt received in findDirsWorker')
        globalfuncs.clearQ(queues['dirsQ'])
    finally:
        # All directories have been scanned.  Send the sentinel message to shutdown the consumer threads
        logger.info('Found %d folders', totals['totalDirs'].value)
        logger.info('Stopping findDirsWorker')

        
def findFilesWorker(config: classes.Config, 
                    queues: dict, 
                    totals: dict,
                    stopEvent: mp.Event,
                   ) -> list:
    '''
    Inputs: Config(config), dirsQ as consumer, filesQ as producer

    Places identified files on the filesQ for one of the file handler workers to pick it up
    '''
    
    
    try:
        logger=logging.getLogger('findFilesWorker')
        logger.addHandler(QueueHandler(queues['logQ']))
        logger.setLevel(config.getLogLevel())
        logger.propagate=False

        logger.info('Starting findFilesWorker')

        while True:
            if stopEvent.is_set():
                break
            try:
                item=queues['dirsQ'].get(timeout=.1)
            except Empty:
                continue

            if item==None:
                break
            
            # Path-ify the directory name
            d=pathlib.Path(item)
            
            try: 
                logger.info('Scanning directory: %s', str(d))
                for f in d.iterdir():
                    screenItem=fileChecks(f, config)           
                    if all(screenItem):
                        mimeType = getMime(f)
                        match=fileMatches(f, config.getFileExts(), config.getMimeTypes())
                        if match:
                            queues['filesQ'].put(classes.File(f, mimeType))
                            totals['totalFilesFound'].value+=1
                        else:
                            logger.debug('%s: Item not added (suffix: %s | mime: %s)', f, f.suffix, mimeType)
                    else:
                        logger.debug('%s: Item failed file checks (isFile=%s, isNotZero=%s, isLocalFile=%s)', f, screenItem[0], screenItem[1], screenItem[2])
            except PermissionError as e:
                logger.debug('PermissionError: %s', str(e))
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.debug('OSError: %s', str(e))

        # All files have been identified.  Put the sentinel on the queue for the fileHandlers 
        queues['filesQ'].put(None)
        globalfuncs.waitOnQ(queues['filesQ'])
    except KeyboardInterrupt:
        console.normal('\n')
        console.warn('User terminated scan.  Shutting down.')
        logger.info('KeyboardInterrupt received in findFilesWorker')
        globalfuncs.clearQ(queues['dirsQ'])
        globalfuncs.clearQ(queues['filesQ'])
    finally:
        logger.info('Found %d files', totals['totalFilesFound'].value)
        logger.info('Stopping findFilesWorker')


def fileChecks(f: pathlib.Path, 
               config: classes.Config) -> tuple:
    '''
    Performs initial checks to see if the item is a file, has content, and is local (e.g. OneDrive file already local file system)
    Returns a tuple of (isFile, isNotZero, isLocalFile)
    '''
    recallMask=0x400000
    offlineMask=0x1000

    isFile = f.is_file() 
    isNotZero = f.stat().st_size > 0

    # Checks if the file is local.  Non-local OneDrive and Dropbox files will be skipped.
    # https://superuser.com/questions/1718444/determining-if-a-onedrive-file-is-synced-locally-via-a-terminal
    # For OneDrive, the 0x400000 (Recall) bit will be set (TRUE) if the file isn't on the disk.
    # For Dropbox, the 0x1000 (Offline) bit will be set (TRUE) if the file isn't on the disk.
    # According to the linked article, this might not be derived from MS documentation, so it could break in the future, but from my own testing, it seems to indicate correctly
    # And the Dropbox behavior is from direct observation
    if win32apiLoaded and config.getLocalFilesOnly():
        try:
            attr=GetFileAttributes(str(f))
        except Exception:
            # The most common exception is that the file is in use, which means its local. But, when in doubt, assume it's a local file.
            isLocalFile=True
        else:
            oneDriveRemote=bool(attr & recallMask)
            dropboxRemote=bool(attr & offlineMask)

            # A file is only remote when either the RECALL or the Offline bit is set.  We need the inverse of that.
            isLocalFile=not(oneDriveRemote or dropboxRemote)
    else:
        isLocalFile=True

    return (isFile, isNotZero, isLocalFile)

def fileMatches(f: pathlib.Path, fileExts: list, mimeTypes: list) -> bool:
    extFound = f.suffix in fileExts
    mimeFound = getMime(f) in mimeTypes

    return extFound or mimeFound


def _filesQStubWorker(queues: dict, totals: dict):
    import random, time
    
    result={'filename': '/path/to/filename.txt',
            'pan': {
                'visa': {'4893 01** **** 6137',}}}
    
    logger=logging.getLogger('filesQStubWorker')
    logger.addHandler(QueueHandler(queues['logQ']))
    logger.propagate=False
    logger.info('Starting filesQStubWorker')

    while True:
        item=queues['filesQ'].get()
        if item == None:
            queues['filesQ'].put(item)
            break
        r=(random.random())
        time.sleep(r/4)
        totals['totalFilesScanned'].value+=1
        if r<.1:
            queues['resultsQ'].put(result)
    
    # Send the sentinel message to the results queue to shutdown
    queues['resultsQ'].put(None)
    logger.info('Stopping filesQStubWorker')

def _resultsQStubWorker(queues: dict, totals: dict):
    logger=logging.getLogger('resultsQStubWorker')
    logger.addHandler(QueueHandler(queues['logQ']))
    logger.propagate=False
    logger.info('Starting resultsQStubWorker')

    while True:
        item=queues['resultsQ'].get()
        if item == None:
            break
        totals['totalResults'].value+=1

    logger.info('Stopping filesQStubWorker')

def main():
    '''
    Testing for the fileparser module.  Normally these options would all be set in the main program or passed to findDirsWorker and findFilesWorker
    '''
    from datetime import datetime
    import os
    start=datetime.now()
    config=classes.Config('dne.toml')
    queues={name: mp.Queue() for name in ['logQ', 'dirsQ', 'filesQ', 'resultsQ']}
    totals={k: mp.Value('i', 0) for k in ['totalDirs', 'totalFilesFound', 'totalFilesScanned', 'totalResults']}
    stopEvent=mp.Event()
    stopEvent.clear()
    
    
    console.normal('File spec to match: %s' % (str(config.getFileExts())))
    console.normal('Mime types to match: %s' % (str(config.getMimeTypes())))
    console.normal('Scanning %s for matching files...' % (config.getStartDirs()))

    #####################################
    # Bring up the pipe line in reverse order -- starting with results and working back to scanning for directories
    #####################################
    processResultsThread=threading.Thread(target=_resultsQStubWorker, args=(queues,totals,), daemon=True)
    processResultsThread.start()

    filesQProcesses=[mp.Process(target=_filesQStubWorker, args=(queues,totals)) for i in range(os.cpu_count())]
    
    #Uncomment for debugging the files queue stub
    #filesQProcesses=[mp.Process(target=_filesQStubWorker, args=(queues,totals))]

    # for process in filesQProcesses:
    #     try:
    #         process.start()
    #     except KeyboardInterrupt:
    #         print('Quitting...')
    #         exit()

    # findFilesThread=threading.Thread(target=findFilesWorker, args=(config, queues, totals))
    # findFilesThread.start()
    
    findDirsThread=threading.Thread(target=findDirsWorker, args=(config, queues, totals, stopEvent))
    findDirsThread.start()

    # Populate the dirsQ with the initial directories from the config.  findFilesWorker will start with these while waiting for findDirs to identify more
    # for item in config.getStartDirs():
    #     queues['dirsQ'].put(item)

    ##################################
    # Wait for the threads/processes to complete by joining each of the threads/processes in the pipeline in pipeline order
    ##################################
    findDirsThread.join()
    # findFilesThread.join()
    # for process in filesQProcesses:
    #     process.join()
    # processResultsThread.join()

    # There will be an extra item in the files queue, so pull it out.  Without this, the queuefeederthread used by MP will not shutdown
    _=queues['filesQ'].get()

    print("\nTotal time: ", datetime.now() - start)

    sys.exit()
    
if __name__ == '__main__':
    main()
