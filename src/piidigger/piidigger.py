import argparse
import multiprocessing as mp
import sys
import textwrap
import traceback
from ctypes import c_int, c_uint64
from datetime import datetime
from os import makedirs, cpu_count
from pathlib import Path
from time import sleep


import json
try:
    from wakepy import keep
    WAKEPY = True
except ImportError:
    WAKEPY = False
    pass

import piidigger.classes as classes
from piidigger import console
from piidigger import filescan
from piidigger import globalfuncs
from piidigger import queuefuncs
from piidigger import __version__
from piidigger.globalvars import errorCodes
from piidigger.globalvars import SENTINEL
from piidigger.logmanager import LogManager


def cleanup(queues: dict,):
    '''Cleans up all processes on ending the program'''

    # Clean up any queues that still have stuff in them.  This will ensure that the mp.Queue-based QueueHandler thread will also terminate
    print()
    for q in queues:
        if not queues[q].empty():
            queuefuncs.clearQ(queues[q])

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


def fileHandlerDispatcher(config: classes.Config,
                          queues: dict,
                          totals: dict,
                          stopEvent: mp.Event,
                          activeFilesQProcesses: mp.Value,
                          logManager: LogManager,
                         ):

    try:
        with activeFilesQProcesses.get_lock():
            activeFilesQProcesses.value+=1
        dataHandlerModules=globalfuncs.getEnabledDataHandlerModules(config.getDataHandlers())

        logger = logManager.getLogger(name=mp.current_process().name)
        logger.debug('Process %s (%s) started (Active=%d)', mp.current_process().name, mp.current_process().pid, activeFilesQProcesses.value)
        resultsQs = [qName for qName in queues.keys() if qName.endswith('_resultsQ')]
        
        while True:
            if stopEvent.is_set():
                break
            item=queuefuncs.getItem(queues['filesQ'])
            if item == SENTINEL:
                break
            if item == None:
                continue

            # Set some variables for this item
            filename=item.getFullPath()
            fileHandlerModule=globalfuncs.getFileHandlerModule(item.getFileHandlerName())
            results={
                'filename': filename,
                'matches': {}
            }
            
            logger.info('[%s]Processing %s with %s', mp.current_process().name, filename, fileHandlerModule.__name__)
            
            for content in fileHandlerModule.readFile(filename, logManager):
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
                with totals['totalResults'].get_lock():
                    totals['totalResults'].value += globalfuncs.countResults(results['matches'])
                for q in resultsQs:
                    queues[q].put(results)

            logger.debug('%s: Processing complete', filename)

    except KeyboardInterrupt:
        pass
    finally:
        logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)
        with activeFilesQProcesses.get_lock():
            activeFilesQProcesses.value-=1
            logger.info('FileHandler processes remaining: %d', activeFilesQProcesses.value)
        if activeFilesQProcesses.value==0:
            # Put the sentinel on the results queues
            for q in [qName for qName in queues.keys() if qName.endswith('_resultsQ')]:
                queues[q].put(SENTINEL)
            
            # To allow the queue to shutdown properly, remove the last item from the filesQ if we're the last filesQ processor still running
            logger.info('[%s]Last FileHandler process terminated.  Clearing filesQ.', mp.current_process().name)
            queuefuncs.clearQ(queues['filesQ'])
        else:
            logger.info('[%s]FileHandler process terminated.  %d FileHandler processes remaining.', mp.current_process().name, activeFilesQProcesses.value)
            # Put another sentinel on the filesQ to signal the next fileHandler process to terminate
            queues['filesQ'].put(SENTINEL)
        del logger


def getOutputHandlers(config: classes.Config,
                      queues: dict,
                      stopEvent: mp.Event,
                      logManager: LogManager,):

    '''Setup the results queue handlers for the enabled output types.
    
    Returns a list of callables which will be added to the ProcessManager for execution.'''
    
    try:
        for resultsType in config.getEnabledOutputTypes():
            try:
                makedirs(str(Path(config.getOutputFile(resultsType)).absolute().parent), exist_ok=True)
            except Exception as e:
                console.error(str(e))
                stopEvent.set()
            yield {
                    'target': globalfuncs.getOutputHandlerModule(resultsType).processResult,
                    'name': resultsType+'_handler', 
                    'num_processes': 1,
                    'args': (config.getOutputFile(resultsType), 
                             queues[resultsType+'_resultsQ'], 
                             stopEvent,
                             logManager,),
                  }
    except KeyboardInterrupt:
        pass


def progressLineWorker(totals: dict, 
                       startTime: datetime,
                       stopEvent: mp.Event,
                       logManager: LogManager,
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
        logger = logManager.getLogger('progressLineWorker')
        logger.info('progressLineWorker started')

        console.normal('If needed, press CTRL-C to terminate scan')

        #_printProgressLine()

        # Removed temporarily as it was having trouble with PyInstaller  
        if globalfuncs.getOSType()=='linux':
            _printProgressLine()
        else:
            if WAKEPY:
                with keep.presenting() as k:
                    if k.success:
                        console.normal('Sleep prevention enabled.')
                    else:
                        console.warn('Sleep prevention was unsuccessful.  System may go to sleep during scan.')
                    _printProgressLine()
            else:
                console.warn('Sleep prevention not available.  System may go to sleep during scan.')
                _printProgressLine()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)

def main():
    start=datetime.now()
    args=commandLineParser()
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

    try:
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
        for resultsType in config.getEnabledOutputTypes():
            name=resultsType+'_resultsQ'
            queues.update({name: mp.Queue()})
        stopEvent=mp.Event()
        stopEvent.clear()
        logManager=LogManager(
            logFile=config.getLogFile(), 
            logLevel=config.getLogLevel(), 
            logQueue=queues['logQ'],)
        
        # Configure logging / the logger process should be managed separately from all of the others.
        loggerPM=classes.ProcessManager(name='loggerPM', 
                                        logManager=logManager,)
        makedirs(str(Path(config.getLogFile()).absolute().parent), exist_ok=True)
        loggerPM.register(target=logManager.logProcessor, 
                    name='logProcessor',
                    num_processes=1, 
                    args=(stopEvent,))
        
        # We need to start the logProcessor process before we can start the other processes
        loggerPM.start()
        logger = logManager.getLogger('main')
            
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
        mainPM=classes.ProcessManager(name='mainPM',
                                      logManager=logManager,)
        for outputHandler in getOutputHandlers(config, queues, stopEvent, logManager):
            mainPM.register(target=outputHandler['target'],
                        name=outputHandler['name'],
                        num_processes=outputHandler['num_processes'],
                        args=outputHandler['args'],)
        mainPM.register(target=filescan.findFilesWorker, 
                    name='findFilesWorker',
                    num_processes=config.getMaxFilesScanProcs(), 
                    args=(config, queues, totals, stopEvent, logManager,),)
        mainPM.register(target=fileHandlerDispatcher, 
                    name='fileHandler',
                    num_processes=config.getMaxProcs(),
                    args=(config, queues, totals, stopEvent, activeFilesQProcesses, logManager,),)
        mainPM.register(target=filescan.findDirsWorker, 
                    name='findDirsWorker',
                    num_processes=1,
                    args=(config, queues, totals, stopEvent, logManager,),)
        console.normal('Starting %d file scanner processes' % (config.getMaxFilesScanProcs()))
        console.normal('Starting %d file handler processes' % (config.getMaxProcs()))

        # Start the progress line worker in a separate process manager
        progressPM=classes.ProcessManager(name='progressPM',
                                          logManager=logManager,)
        progressPM.register(target=progressLineWorker, 
                    name='progressLineWorker',
                    num_processes=1, 
                    args=(totals, start, stopEvent, logManager,),)
        
        # Start the processes
        progressPM.start()
        mainPM.start()

        ##################################
        # Wait for the main program to finish
        ##################################

        mainPM.wait_for_processes()
    except KeyboardInterrupt:
        # If the keyboard interrupt was early enought, maybe not all of the process managers have been started
        # So wrap the cleanup in a try/except block to catch undeclared variables
        try:
            progressPM.terminate_all_processes()
            mainPM.terminate_all_processes()
            loggerPM.terminate_all_processes()
        except UnboundLocalError:
            pass
    except Exception:
        console.error('An unknown error was encountered.  Error message was captured in %s.' % config.getLogFile())
        logger.error(traceback.print_exc())
    else:
        queues['logQ'].put(SENTINEL)
        # If the logger hasn't already been shutdown by a KeyboardInterrupt or other event
        stopEvent.set()
        progressPM.wait_for_processes()
    finally:
        try:
            loggerPM.wait_for_processes()
            cleanup(queues)
        except UnboundLocalError:
            pass
        console.normal('Scan complete.')
            
if __name__ == '__main__':
    mp.freeze_support()
    main()