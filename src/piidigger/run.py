import json
import multiprocessing as mp
import traceback
from ctypes import c_int, c_uint64
from datetime import datetime
from os import makedirs
from pathlib import Path
from time import sleep

try:
    from wakepy import keep
    WAKEPY = True
except ImportError:
    WAKEPY = False

import piidigger.classes as classes
from piidigger import __version__, console, filescan, globalfuncs, queuefuncs
from piidigger.globalvars import ERROR_CODES, SENTINEL
from piidigger.logmanager import LogManager


def _cleanup(queues: dict) -> None:
    print()
    for q in queues:
        if not queues[q].empty():
            queuefuncs.clearQ(queues[q])


def _file_handler_dispatcher(config: classes.Config,
                              queues: dict,
                              totals: dict,
                              stop_event: mp.Event,
                              active_files_q_processes: mp.Value,
                              log_manager: LogManager,
                              ) -> None:
    try:
        with active_files_q_processes.get_lock():
            active_files_q_processes.value += 1
        data_handler_modules = globalfuncs.get_enabled_data_handler_modules(config.getDataHandlers())

        logger = log_manager.getLogger(name=mp.current_process().name)
        logger.debug('Process %s (%s) started (Active=%d)', mp.current_process().name, mp.current_process().pid, active_files_q_processes.value)
        results_qs = [q_name for q_name in queues.keys() if q_name.endswith('_resultsQ')]

        while True:
            if stop_event.is_set():
                break
            item = queuefuncs.getItem(queues['filesQ'])
            if item == SENTINEL:
                break
            if item is None:
                continue

            filename = item.getFullPath()
            file_handler_module = globalfuncs.get_file_handler_module(item.getFileHandlerName())
            results = {
                'filename': filename,
                'matches': {}
            }

            logger.info('[%s]Processing %s with %s', mp.current_process().name, filename, file_handler_module.__name__)

            for content in file_handler_module.read_file(filename, log_manager):
                logger.debug('%s: Received %d bytes from file handler', filename, len(content))
                if content == '':
                    break
                for handler in data_handler_modules:
                    results = globalfuncs.process_matches(results, handler.find_match(content), handler.dh_name)

            with totals['filesScanned'].get_lock():
                totals['filesScanned'].value += 1
            with totals['bytesScanned'].get_lock():
                totals['bytesScanned'].value += item.getFileSize()

            if len(results['matches']) > 0:
                logger.debug('%s: %s matches found', filename, str(results['matches'].keys()))
                logger.debug('%s: Rebuilding result sets into lists', filename)
                for handler in results['matches']:
                    for key in results['matches'][handler]:
                        match_list = list(results['matches'][handler][key])
                        results['matches'][handler][key] = match_list

                with totals['totalResults'].get_lock():
                    totals['totalResults'].value += globalfuncs.count_results(results['matches'])
                for q in results_qs:
                    queues[q].put(results)

            logger.debug('%s: Processing complete', filename)

    except KeyboardInterrupt:
        pass
    finally:
        logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)
        with active_files_q_processes.get_lock():
            active_files_q_processes.value -= 1
            logger.info('FileHandler processes remaining: %d', active_files_q_processes.value)
        if active_files_q_processes.value == 0:
            for q in [q_name for q_name in queues.keys() if q_name.endswith('_resultsQ')]:
                queues[q].put(SENTINEL)
            logger.info('[%s]Last FileHandler process terminated.  Clearing filesQ.', mp.current_process().name)
            queuefuncs.clearQ(queues['filesQ'])
        else:
            logger.info('[%s]FileHandler process terminated.  %d FileHandler processes remaining.', mp.current_process().name, active_files_q_processes.value)
            queues['filesQ'].put(SENTINEL)
        del logger


def _get_output_handlers(config: classes.Config,
                          queues: dict,
                          stop_event: mp.Event,
                          log_manager: LogManager,
                          ):
    try:
        for results_type in config.getEnabledOutputTypes():
            try:
                makedirs(str(Path(config.getOutputFile(results_type)).absolute().parent), exist_ok=True)
            except Exception as e:
                console.error(str(e))
                stop_event.set()
            yield {
                'target': globalfuncs.get_output_handler_module(results_type).process_result,
                'name': results_type + '_handler',
                'num_processes': 1,
                'args': (config.getOutputFile(results_type),
                         queues[results_type + '_resultsQ'],
                         stop_event,
                         log_manager,),
            }
    except KeyboardInterrupt:
        pass


def _progress_line_worker(totals: dict,
                           start_time: datetime,
                           stop_event: mp.Event,
                           log_manager: LogManager,
                           ) -> None:
    def _print_progress_line() -> None:
        interval = 1
        last_line_len = 0

        while True:
            screen_width = console.get_terminal_size()[0]
            line = '{} | Folders scanned: {:,}/{:,} | Files scanned: {:,}/{:,} ({}/{}) | Results found: {}'.format(
                str(datetime.now() - start_time).split('.')[0],
                totals['dirsScanned'].value, totals['dirsFound'].value,
                totals['filesScanned'].value, totals['filesFound'].value,
                globalfuncs.sizeof_fmt(totals['bytesScanned'].value), globalfuncs.sizeof_fmt(totals['bytesFound'].value),
                totals['totalResults'].value)
            if len(line) > screen_width:
                line = line[:screen_width - 1]

            console.status(line + ' ' * (last_line_len - len(line)))
            last_line_len = len(line)

            if stop_event.is_set():
                break
            sleep(interval)

    try:
        logger = log_manager.getLogger('progressLineWorker')
        logger.info('progressLineWorker started')

        console.normal('If needed, press CTRL-C to terminate scan')

        if globalfuncs.get_os_type() == 'linux':
            _print_progress_line()
        else:
            if WAKEPY:
                with keep.presenting() as k:
                    if k.active:
                        console.normal('Sleep prevention enabled.')
                    else:
                        console.warn('Sleep prevention was unsuccessful.  System may go to sleep during scan.')
                    _print_progress_line()
            else:
                console.warn('Sleep prevention not available.  System may go to sleep during scan.')
                _print_progress_line()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info('Stopping %s (PID=%d)', mp.current_process().name, mp.current_process().pid)


def run_scan(config: classes.Config) -> int:
    """Run a PII scan against the provided Config. Returns an exit code."""
    start = datetime.now()
    try:
        totals = {k: mp.Value(c_uint64, 0) for k in [
            'dirsScanned',
            'dirsFound',
            'filesScanned',
            'filesFound',
            'bytesScanned',
            'bytesFound',
            'totalResults']}
        queues = {name: mp.Queue() for name in ['logQ', 'dirsQ', 'filesQ', 'totalsQ']}
        active_files_q_processes = mp.Value(c_int, 0)
        for results_type in config.getEnabledOutputTypes():
            name = results_type + '_resultsQ'
            queues.update({name: mp.Queue()})
        stop_event = mp.Event()
        stop_event.clear()
        log_manager = LogManager(
            logFile=config.getLogFile(),
            logLevel=config.getLogLevel(),
            logQueue=queues['logQ'],)

        logger_pm = classes.ProcessManager(name='loggerPM', logManager=log_manager)
        makedirs(str(Path(config.getLogFile()).absolute().parent), exist_ok=True)
        logger_pm.register(target=log_manager.logProcessor,
                           name='logProcessor',
                           num_processes=1,
                           args=(stop_event,))
        logger_pm.start()
        logger = log_manager.getLogger('main')

        logger.info('Starting PIIDigger version %s', __version__)
        logger.info("Configuration: %s", json.dumps(config.getConfig()))

        if len(config.getMimeTypes()) == 0:
            logger.info("MIME detection disabled.")

        if not globalfuncs.is_admin():
            message = 'Not running as an administrator. File system access may be restricted.'
            console.warn(message)
            logger.info(message)

        if globalfuncs.get_os_type() == 'linux':
            console.warn('Sleep prevention disabled on Linux. Consider using \'screen\' or \'tmux\' to ensure that PIIDigger survives an SSH disconnect.')

        console.normal(f'Scanning {config.getStartDirs()} for files matching {config.getDataHandlers()}')

        main_pm = classes.ProcessManager(name='mainPM', logManager=log_manager)
        for output_handler in _get_output_handlers(config, queues, stop_event, log_manager):
            main_pm.register(target=output_handler['target'],
                             name=output_handler['name'],
                             num_processes=output_handler['num_processes'],
                             args=output_handler['args'])
        main_pm.register(target=filescan.findFilesWorker,
                         name='findFilesWorker',
                         num_processes=config.getMaxFilesScanProcs(),
                         args=(config, queues, totals, stop_event, log_manager,))
        main_pm.register(target=_file_handler_dispatcher,
                         name='fileHandler',
                         num_processes=config.getMaxProcs(),
                         args=(config, queues, totals, stop_event, active_files_q_processes, log_manager,))
        main_pm.register(target=filescan.findDirsWorker,
                         name='findDirsWorker',
                         num_processes=1,
                         args=(config, queues, totals, stop_event, log_manager,))
        console.normal(f'Starting {config.getMaxFilesScanProcs()} file scanner processes')
        console.normal(f'Starting {config.getMaxProcs()} file handler processes')

        progress_pm = classes.ProcessManager(name='progressPM', logManager=log_manager)
        progress_pm.register(target=_progress_line_worker,
                              name='progressLineWorker',
                              num_processes=1,
                              args=(totals, start, stop_event, log_manager,))

        progress_pm.start()
        main_pm.start()

        main_pm.wait_for_processes()
    except KeyboardInterrupt:
        try:
            progress_pm.terminate_all_processes()
            main_pm.terminate_all_processes()
            logger_pm.terminate_all_processes()
        except UnboundLocalError:
            pass
    except Exception:
        console.error(f'An unknown error was encountered.  Error message was captured in {config.getLogFile()}.')
        logger.error(traceback.print_exc())
    else:
        queues['logQ'].put(SENTINEL)
        stop_event.set()
        progress_pm.wait_for_processes()
    finally:
        try:
            logger_pm.wait_for_processes()
            _cleanup(queues)
        except UnboundLocalError:
            pass
        console.normal('Scan complete.')

    return ERROR_CODES['ok']
