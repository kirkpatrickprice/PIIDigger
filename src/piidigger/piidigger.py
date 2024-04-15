import argparse
import logging
import multiprocessing as mp
import sys
import textwrap
import traceback
from ctypes import c_int, c_uint64
from datetime import datetime
from logging.handlers import QueueHandler
from os import makedirs, cpu_count
from pathlib import Path
from time import sleep


import json
# Removed temporarily as it was having trouble with PyInstaller
from wakepy import keep


import piidigger.classes as classes
from piidigger import (
    console,
    filescan,
    globalfuncs,
    __version__,
    )
from piidigger.globalvars import (
    errorCodes,
    )


def cleanup(processes: dict,
            queues: dict,
            stopEvent: mp.Event,
            abort: bool=False,
           ):
    '''Cleans up all processes on ending the program'''

    #Set the stopEvent so that processes have a chance to shutdown normally
    stopEvent.set()

    if abort:
        sleep(.5)
        for processType in processes:
            for process in processes[processType]:
                if process.is_alive():
                    console.normal('Terminating process: %s' % process.name)
                    process.terminate()

    try:
        processes['progressLine'][0].join()
    except KeyboardInterrupt:
        pass

    # Clean up any queues that still have stuff in them.  This will ensure that the mp.Queue-based QueueHandler thread will also terminate
    print()
    for q in queues:
        if not queues[q].empty():
            console.normal('Cleaning up queue: %s' % q)
            globalfuncs.clearQ(queues[q])

def commandLineParser() -> argparse.ArgumentParser:
    '''Handles the command line options'''
    parser = argparse.ArgumentParser(
    prog=sys.argv[0].split('/')[-1],
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=textwrap.dedent('''
        Search the file system for Personally Identifiable Information

        NOTES:
            * All program configuration is kept in \'piidigger.toml\' -- a TOML-formatted configuration file
            * A default configuration will be used if the default 'piidigger.toml' file doesn't exist
        ''')
    )

    configControl = parser.add_argument_group(title='Configuration')
    configControl.add_argument(
        '-c', '--create-conf',
        dest='createConfigFile',
        default='',
        help='Create a default configuration file for editing/reuse.',
        )
    configControl.add_argument(
        '-d', '--default-conf',
        dest='defaultConfig',
        action='store_true',
        help='Use the default, internal config.'
        )
    configControl.add_argument(
        '-f', '--conf-file', 
        dest='configFile', 
        default='piidigger.toml', 
        help='path/to/configfile.toml configuration file (Default = "piidigger.toml").  If the file is not found, the default, internal configuration will be used.'
        )
    configControl.add_argument(
        '-p', '--max-process',
        dest='maxProc',
        default=0,
        type=int,
        help='Override the number processes to use for searching files.  Will use the lesser of CPU cores or this value.  On production servers, consider setting this to less than the number of physical CPUs.  See \'--cpu-count\' below.',
        )
    
    miscInfoControl = parser.add_argument_group(title='Misc. Info')
    miscInfoControl.add_argument(
        '--cpu-count',
        dest='cpuCount',
        action='store_true',
        help='Show the number of logical CPUs provided by the OS.  Use this to tune performance.  See \'--max-process\' above.'
    )
    miscInfoControl.add_argument(
        '--list-datahandlers',
        dest='listDH',
        action='store_true',
        help='Display the list of data handlers and exit'
    )
    miscInfoControl.add_argument(
        '--list-filetypes',
        dest='listFT',
        action='store_true',
        help='Display the list of file types and exit'
    )
    miscInfoControl.add_argument(
        '--version', '-v',
        dest='version',
        action='store_true',
        help='Display the version number and exit'
    )
    
    return parser.parse_args()


def logProcessor(config: classes.Config, 
                 queue: mp.Queue,
                 stopEvent: mp.Event):
    try:
        logger=logging.getLogger()

        logFileFormatter=logging.Formatter('%(asctime)s:[%(name)s]:%(levelname)s:%(message)s')
        logFileHandler=logging.FileHandler(filename=config.getLogFile(),mode='w',encoding='utf-8')
        logFileHandler.setFormatter(logFileFormatter)

        logger.setLevel(config.getLogLevel())
        logger.addHandler(logFileHandler)

        logger.info('Starting logProcessor (%s)', mp.current_process().pid)
        stopCause=None

        while True:
            if stopEvent.is_set():
                stopCause = 'stopEvent'
                break

            message = queue.get(1)

            if message == None:
                stopCause = 'endQueue'
                break

            logger.handle(message)
    except KeyboardInterrupt:
        # Give other processes a chance to write their final messages to the queue
        sleep(5)
        globalfuncs.clearQ(queue)
        stopCause='ctrlc'
        logger.info('[logProcessor]User terminated scan')
    finally:
        logger.info('[logProcessor]Stopping logProcessor (%s)', str(stopCause))
        

def fileHandlerDispatcher(config: classes.Config,
                          queues: dict,
                          totals: dict,
                          stopEvent: mp.Event,
                          activeFilesQProcesses: mp.Value,
                         ):

    try:
        with activeFilesQProcesses.get_lock():
            activeFilesQProcesses.value+=1
        dataHandlerModules=globalfuncs.getEnabledDataHandlerModules(config.getDataHandlers())

        logger=logging.getLogger('fileHandlerDispatch')
        if not logger.handlers:
            logger.addHandler(QueueHandler(queues['logQ']))
        logger.setLevel(config.getLogLevel())
        logger.propagate=False
        logger.debug('Process %s (%s) started (Active=%d)', mp.current_process().name, mp.current_process().pid, activeFilesQProcesses.value)
        resultsQs = [qName for qName in queues.keys() if qName.endswith('_resultsQ')]
        
        logConfig={'q': queues['logQ'],
                'level': config.getLogLevel(),
                }

        while True:
            if stopEvent.is_set():
                break
            item=queues['filesQ'].get()
            if item == None:
                queues['filesQ'].put(item)
                break

            # Set some variables for this item
            filename=item.getFullPath()
            fileHandlerModule=globalfuncs.getFileHandlerModule(item.getFileHandlerName())
            results={
                'filename': filename,
                'matches': {}
            }
            
            logger.info('[%s]Processing %s with %s', mp.current_process().name, filename, fileHandlerModule.__name__)
            
            for content in fileHandlerModule.readFile(filename, logConfig):
                logger.debug('%s: Received %d bytes from file hander', filename, len(content))

                if content == '':
                    break
                
                # chunks=globalfuncs.makeChunks(content)
                # logger.debug('%s: Created %d chunks', filename, len(chunks))

                # We only need chunks from here out.  Conserve a little memory by deleting "content"
                #del content

                for handler in dataHandlerModules:
                    #logger.debug('%s: Processing %d chunks with %s', filename, len(chunks), handler.dhName)
                    # for chunk in chunks:
                    #     results=globalfuncs.processMatches(results, handler.findMatch(chunk), handler.dhName)
                    results=globalfuncs.processMatches(results, handler.findMatch(content), handler.dhName)
                    # logger.debug('%s: %d chunks processed with %s', filename, len(chunks), handler.dhName)

            # Update the status counters
            with totals['filesScanned'].get_lock():
                totals['filesScanned'].value+=1
            with totals['bytesScanned'].get_lock():
                totals['bytesScanned'].value+=item.getFileSize()
            
            # Submit the results to outputhandlers
            if len(results['matches']) > 0:
                logger.debug('%s: %s matches found', filename, str(results['matches'].keys()))
                
                # Since Python sets aren't serializable as a JSON object type, we'll convert our results to Lists now.
                logger.debug('%s: Rebuilding result sets into lists', filename)
                for handler in results['matches']:
                    for key in results['matches'][handler]:
                        l=list(results['matches'][handler][key])
                        results['matches'][handler][key]=l

                # Update the results totals
                totals['totalResults'].value += globalfuncs.countResults(results['matches'])
                for q in resultsQs:
                    queues[q].put(results)

            logger.debug('%s: Processing complete', filename)

    except KeyboardInterrupt:
        for q in [qName for qName in queues.keys() if qName.endswith('_resultsQ')]:
            globalfuncs.clearQ(queues[q])
    finally:
        logger.info('[%s]FileWorkerProcess terminated', mp.current_process().name)
        with activeFilesQProcesses.get_lock():
            activeFilesQProcesses.value-=1
            logger.info('FileWorkerProcesses remaining: %d', activeFilesQProcesses.value)
        # To allow the queue to shutdown properly, remove the last item from the filesQ if we're the last filesQ processor still running
        if activeFilesQProcesses.value==0:
            logger.debug('[%s]Last FileWorkerProcess terminated.  Clearing filesQ.', mp.current_process().name)
            queues['filesQ'].get()
        del logger


def resultsDispatcher(config: classes.Config, 
                      queues: dict,
                      stopEvent: mp.Event,
                     ) -> list:

    '''Setup the results queue handlers for the enabled output types.
    
    Returns a list of mp.Process() objects which will be started later'''
    
    try:
        logger=logging.getLogger('resultsDispatcher')
        logger.addHandler(QueueHandler(queues['logQ']))
        logger.propagate=False
        
        procs=list()

        for resultsType in config.getEnabledOutputTypes():
            try:
                makedirs(str(Path(config.getOutputFile(resultsType)).absolute().parent), exist_ok=True)
            except Exception as e:
                console.error(str(e))
                stopEvent.set()
            module=globalfuncs.getOutputHandlerModule(resultsType)
            qName=resultsType+'_resultsQ'
            proc=mp.Process(target=module.processResult, name=resultsType+'_process', args=(config.getOutputFile(resultsType), queues[qName], stopEvent), daemon=True)
            procs.append(proc)
    except KeyboardInterrupt:
        pass

    return procs


def progressLineWorker(totals: dict, 
                       logQ: mp.Queue, 
                       startTime: datetime,
                       stopEvent: mp.Event,
                      ):

    def _printProgressLine():
        INTERVAL = 1
        lastLineLen = 0

        while True:
            screenWidth=console.getTerminalSize()[0]
            line='{} | Folders scanned: {:,}/{:,} | Files scanned: {:,}/{:,} ({}/{}) | Results found: {}'.format(
                str(datetime.now() - startTime).split('.')[0], 
                totals['dirsScanned'].value, totals['dirsFound'].value, 
                totals['filesScanned'].value, totals['filesFound'].value, 
                globalfuncs.sizeof_fmt(totals['bytesScanned'].value), globalfuncs.sizeof_fmt(totals['bytesFound'].value),
                totals['totalResults'].value)
            if len(line) > screenWidth:
                line=line[:screenWidth-1]

            console.status(line+' '*(lastLineLen-len(line)))
            lastLineLen=len(line)

            #Placing the event handler at the end will ensure that the last update is processed before terminating the thread
            if stopEvent.is_set():
                break
            sleep(INTERVAL)

    try:
        logger=logging.getLogger('progressLineWorker')
        logger.addHandler(QueueHandler(logQ))
        logger.propagate=False
        logger.info('progressLineWorker started')

        console.normal('If needed, press CTRL-C to terminate scan')

        #_printProgressLine()

        # Removed temporarily as it was having trouble with PyInstaller  
        if globalfuncs.getOSType()=='linux':
            _printProgressLine()
        else:
            with keep.presenting() as k:
                if k.success:
                    console.normal('Sleep prevention enabled.')
                else:
                    console.warn('Sleep prevention was unsuccessful.  System may go to sleep during scan.')
                _printProgressLine()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info('progressLineWorker stopped')

def main():
    start=datetime.now()
    args=commandLineParser()
    CLEANED=False

    if args.cpuCount:
        print('CPU cores:', cpu_count())
        sys.exit(errorCodes['ok'])

    if args.listDH:
        print('Data handler modules: ', globalfuncs.getSupportedDataHandlerNames())
        sys.exit(errorCodes['ok'])

    if args.listFT:
        print('File extns: ', globalfuncs.getSupportedFileExts())
        print('MIME types: ', globalfuncs.getSupportedFileMimes())
        sys.exit(errorCodes['ok'])

    if args.version:
        print('PIIDigger version:', __version__)
        sys.exit(errorCodes['ok'])

    if len(args.createConfigFile) >0:
        tomlFile = str(args.createConfigFile) if str(args.createConfigFile).endswith('.toml') else str(args.createConfigFile)+'.toml'
        configFileWritten=globalfuncs.writeDefaultConfig(tomlFile)

        if configFileWritten=='Success':
            console.normal('Default configuration written to '+args.createConfigFile)
            sys.exit(errorCodes['ok'])
        else:
            console.error('Config file not written: %s' % (configFileWritten))
            sys.exit(errorCodes['unknown'])

    if args.defaultConfig:
        config=classes.Config(configFile='', useDefault=True)
    else:
        config=classes.Config(configFile=args.configFile)

    if args.maxProc>0:
        config.setMaxProcs(min(cpu_count(), args.maxProc))

    # Create queues and other structures needed for asynchronous implementation
    totals={k: mp.Value(c_uint64, 0) for k in [
        'dirsScanned', 
        'dirsFound', 
        'filesScanned', 
        'filesFound', 
        'bytesScanned', 
        'bytesFound',
        'totalResults']}
    queues={name: mp.Queue() for name in ['logQ', 'dirsQ', 'filesQ', 'totalsQ',]}
    activeFilesQProcesses=mp.Value(c_int, 0)
    processes = dict()
    for resultsType in config.getEnabledOutputTypes():
        name=resultsType+'_resultsQ'
        queues.update({name: mp.Queue()})
    stopEvent=mp.Event()
    stopEvent.clear()

    # Configure logging
    makedirs(str(Path(config.getLogFile()).absolute().parent), exist_ok=True)
    processes['logger'] = [mp.Process(target=logProcessor, args=(config, queues['logQ'], stopEvent), name='logger_process', daemon=True)]
    processes['logger'][0].start()
    logger=logging.getLogger()
    logger.setLevel(config.getLogLevel())
    logger.addHandler(QueueHandler(queues['logQ']))
    logger=logging.getLogger('main')
        
    logger.info('Command line arguments: %s', sys.argv[1:])
    logger.info('Starting PIIDigger version %s', __version__)
    logger.info("Configuration: %s", json.dumps(config.getConfig()))

    if len(config.getMimeTypes()) == 0:        
        logger.info("MIME detection disabled.")

    if not globalfuncs.isAdmin():
        message='Not running as an administrator. File system access may be restricted.'
        console.warn(message)
        logger.info(message)

    if globalfuncs.getOSType() == 'linux':
        console.warn('Sleep prevention disabled on Linux. Consider using \'screen\' or \'tmux\' to ensure that PIIDigger survives an SSH disconnect.')
    
    console.normal('Scanning %s for files matching %s' % (config.getStartDirs(), config.getDataHandlers()))
    
    #####################################
    # Bring up the pipe line in reverse order -- starting with results and working back to scanning for directories
    #####################################
        
    # Setup each of the subprocesses that are needed
    processes['progressLine']=[mp.Process(target=progressLineWorker, args=(totals,queues['logQ'], start, stopEvent), name='progressLine_process', daemon=True)]
    processes['outputHandlers']=resultsDispatcher(config, queues, stopEvent)
    processes['findFiles']=[mp.Process(target=filescan.findFilesWorker, args=(config, queues, totals, stopEvent,), name='findFiles'+str(i)+'_process', daemon=True) for i in range(config.getMaxFilesScanProcs())]
    processes['filesQWorkers']=[mp.Process(target=fileHandlerDispatcher, args=(config, queues, totals, stopEvent, activeFilesQProcesses), name='fileHandler'+str(i)+'_process', daemon=True) for i in range(config.getMaxProcs())]
    processes['findDirs']=[mp.Process(target=filescan.findDirsWorker, args=(config, queues, totals, stopEvent,), name='findDirs_process', daemon=True)]
    console.normal('Starting %d file scanner processes' % (config.getMaxFilesScanProcs()))
    console.normal('Starting %d file handler processes' % (config.getMaxProcs()))

    # Start each process in the processes dictionary
    for processType in processes:
        for process in processes[processType]:
            try:
                if not process.is_alive():
                    process.start()
                    logger.info('Starting process %s (%s)', process.name, process.pid)
            except Exception as e:
                logger.info('Error starting %s: %s', process.name, str(e))


    ##################################
    # Wait for the threads/processes to complete by joining each of the threads/processes in the pipeline in pipeline order
    ##################################

    try:
        for processType in ['findDirs', 'findFiles', 'filesQWorkers']:
            for process in processes[processType]:
                logger.info('Joining process %s (%s)', process.name, process.pid)
                process.join()
                logger.info('Returned from process: %s (%s)', process.name, process.pid)
    except KeyboardInterrupt:
        #console.normal('\nUser terminated scan')
        cleanup(processes, queues, stopEvent, abort=True)
        CLEANED=True
    except Exception:
        console.error('An unknown error was encountered.  Error message was captured in %s.' % config.getLogFile())
        logger.error(traceback.print_exc())
    else:
        # Send the sentinel to the results queues and then block until they shutdown properly.
        for q in [qName for qName in queues.keys() if qName.endswith('_resultsQ')]:
            queues[q].put(None)        
        for process in processes['outputHandlers']:
            process.join()
    finally:
        # If the logger hasn't already been shutdown by a KeyboardInterrupt or other event
        if processes['logger'][0].is_alive():
            logger.info('Scanned %d files for %s', totals['filesScanned'].value, globalfuncs.sizeof_fmt(totals['bytesScanned'].value))
            logger.info('Found %d files with matching content', totals['totalResults'].value)

            # Terminate the logProcessor and progress line worker
            logger.info('Scan complete.  Shutting down.')
            queues['logQ'].put(None)
        
        stopEvent.set()        
        processes['progressLine'][0].join()
        console.normal('\nWaiting for logger process to terminate.')
        
        # Make sure all of the queues are empty so that the MPQueueing support threads will shutdown
        if not CLEANED: 
            cleanup(processes, queues, stopEvent)

        console.normal('Scan complete.')
            
if __name__ == '__main__':
    mp.freeze_support()
    main()