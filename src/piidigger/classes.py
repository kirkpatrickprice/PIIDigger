import datetime
import logging
import multiprocessing as mp
import os
import pathlib
import platform
import string

# try:
#     # If we're on Python later than 3.11 then tomllib is included.  We'll use the "toml" namespace so that the same code will work on older Pythons where toml had to be installed.
#     import tomllib as toml
# except ModuleNotFoundError:
import tomli

from piidigger import globalfuncs
from piidigger import console
from piidigger.getmime import testMagic

logger=logging.getLogger(__name__)

class File:
    def __init__(self, f: pathlib.Path, mimeType: str):
        self.filename=f.name
        self.path=f.parent
        self.ext=f.suffix
        self.mimeType=mimeType
        self.handler=globalfuncs.getFileHandlerName(self.ext, self.mimeType)
        self.times=(f.stat().st_atime, f.stat().st_mtime)
        logger.debug('Initialized File object for %s, mimeType=%s, times=%s, handler=%s', self.getFullPath(), self.mimeType, str(self.times), self.handler)
        self.size=f.stat().st_size
        
    def __lt__(self, other):
        return self.getFullPath() < other.getFullPath()

    def getFullPath(self):
        return os.path.join(self.path, self.filename)
    
    def getExtension(self):
        return self.ext
    
    def getOldAccessTime(self):
        return self.times[0]
    
    def getOldModTime(self):
        return self.times[1]
    
    def getFileHandlerName(self) -> str:
        return self.handler
    
    
    def getFileSize(self):
        return self.size
    
    def getTimeStamps(self) -> tuple:
        return self.times

class Config:
    def __init__(self, configFile: str, useDefault: bool=False,):
        
        if useDefault:
            console.normal('Using default configuration.')
            self.config=globalfuncs.getDefaultConfig()
        else:
            try:
                with open(configFile, 'rb') as file:
                    self.config = tomli.load(file)
            except FileNotFoundError:
                console.warn('Configuration file %s not found. Using default configuration.' % configFile)
                configFile = 'Internal Config'
                self.config=globalfuncs.getDefaultConfig()
            except tomli.TOMLDecodeError as e:
                console.error('Invalid configuration (%s)' % (configFile))
                console.error(str(e))
                exit(globalfuncs.errorCodes['invalidConfig'])
        
        self.config['rootPath']=str(pathlib.Path(os.getcwd()).absolute())
        self.config['maxProcs']=os.cpu_count()
        hostname=str(platform.node())
        self.config['hostname']=hostname
        timeStamp=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        
        if _isAll(self.config['dataHandlers']):
            self.config['dataHandlers'] = globalfuncs.getSupportedDataHandlerNames()
        else:
            configHandlers=self.config['dataHandlers']
            notFound = [n for n in configHandlers if not n in globalfuncs.getSupportedDataHandlerNames()]
            if notFound:
                console.error("Unexpected data handler found in configuration file (%s)" % (configFile))
                console.error("The following data handlers will be ignored: " + str(notFound))
                self.config['dataHandlers'] = [n for n in configHandlers if n not in notFound]
        
        # Build the file names for each of the supported result file types
        outpath=self.config['results']['path']
        if not outpath.endswith('/'):
            outpath+='/'
        
        # We don't need this item now and it'll mess up the output file function if we leave it in.
        del self.config['results']['path']
        fileTypes=[('json', '.json'), 
                   ('text', '.txt')]

        for resultType, ext in fileTypes:
            filename=f"{outpath}{hostname}-{timeStamp}{ext}"
            # As set in the TOML file, the different results types are booleans.  We'll replace them with their pathname equiv if they were "enabled" to begin with.
            if self.config['results'][resultType]:
                self.config['results'][resultType]=filename
            else:
                del self.config['results'][type]

        # Fix any Windows paths replacing ALL with the actual drive letters and confirming that hard-coded starting directories exist
        if _isAll(self.config['includeFiles']['startDirs']['windows']):
            self.config['includeFiles']['startDirs']['windows'] = ['%s:\\' % d for d in string.ascii_uppercase if os.path.exists('%s:' % d)]        
        elif not all([os.path.isdir(d) for d in self.getStartDirs()]):
            console.error("Starting directory does not exist (%s). Check configuration file (%s)." % (self.getStartDirs(), configFile))
            exit(globalfuncs.errorCodes['invalidConfig'])


        # Replace "all" extensions with those currently supported by File Handlers
        if _isAll(self.config['includeFiles']['ext']):
            self.config['includeFiles']['ext'] = globalfuncs.getSupportedFileExts()
        else:
            # Before comparing to the expected list, fix the YAML-provided extensions if they don't start with a period
            configExts=[c if c.startswith('.') else '.' + c for c in self.config['includeFiles']['ext']]
            notFound = [n for n in configExts if not n in globalfuncs.getSupportedFileExts()]
            if notFound:
                console.error("Unexpected file extensions found in configuration file (%s)" % (configFile))
                console.error("The following file extensions will be ignored: " + str(notFound))
                self.config['includeFiles']['ext'] = [n for n in configExts if n not in notFound]
        
        # Replace "all" MIME types with those currently supported by File Handlers
        if testMagic():
            if _isAll(self.config['includeFiles']['mime']):
                self.config['includeFiles']['mime'] = globalfuncs.getSupportedFileMimes()
            else:
                configMimes=self.config['includeFiles']['mime']
                notFound = [n for n in configMimes if not n in globalfuncs.getSupportedFileMimes()]
                if notFound:
                    console.error("Unexpected MIME types found in configuration file (%s)" % (configFile))
                    console.error("The following MIME types will be ignored: " + str(notFound))
                    self.config['includeFiles']['mime'] = [n for n in configMimes if n not in notFound]
        else:
            self.config['includeFiles']['mime']=[]

        # Add Results and Log folders to the list of folders to exlude
        self.config['excludeDirs'][globalfuncs.getOSType()].append(str(pathlib.Path(self.getRootPath()) / outpath))
        self.config['excludeDirs'][globalfuncs.getOSType()].append(str(pathlib.Path(pathlib.Path(self.getRootPath()) / self.getLogFile()).parent))
 
    def getDataHandlers(self):
        return self.config['dataHandlers']
    
    def getEnabledOutputTypes(self):
        #return [key for key in self.config['results'].keys()]
        return self.config['results'].keys()
 
    def getExcludeDirs(self):
        return self.config['excludeDirs'][globalfuncs.getOSType()]
 
    def getFileExts(self):
        return self.config['includeFiles']['ext']
 
    def getConfig(self):
        return self.config
 
    def getLocalFilesOnly(self):
        return self.config['localFilesOnly']
    
    def getLogLevel(self):
        return self.config['logging']['logLevel']
 
    def getLogFile(self):
        return self.config['logging']['logFile']

    def getMaxFilesScanProcs(self):
        #return max(self.getMaxProcs() // 4, 1)
        return 1

    def getMaxProcs(self):
        return self.config['maxProcs']

    def getMimeTypes(self):
        return self.config['includeFiles']['mime']

    def getOutputFile(self, resultType: str = ""):
        if resultType:
            return self.config['results'][resultType]
        else:
            return self.config['results']

    def getRootPath(self):
        return self.config['rootPath']

    def getStartDirs(self):
        return self.config['includeFiles']['startDirs'][globalfuncs.getOSType()]
    
    def setMaxProcs(self, procs):
        self.config['maxProcs']=procs

class ProcessManager:
    def __init__(self, 
                 name: str,
                 logQ: mp.Queue,
                 logLevel: str):
        
        self.processes: list = []
        self.name = name
        self.logger=logging.getLogger(name)
        self.logger.addHandler(logging.handlers.QueueHandler(logQ))
        self.logger.setLevel(logLevel)
        self.logger.propagate=False
        self.logger.debug(f'Initialized ProcessManager {name}.')
        
        
    def register(self, 
                 *,
                 target: callable,
                 name: str,
                 num_processes: int, 
                 args: tuple = None
                 ):
        
        p={
            'target': target,
            'start_order': len(self.processes) + 1,
            'shutdown_order': None,
            'name': name,
            'num_processes': num_processes,
            'processes': [],
            'started': False,
            'args': args,
            }
        
        self.processes.append(p)
        self.logger.debug(f'{self.name}: Registered process {name} with {num_processes} processes.')

    def start(self):
        try:
            self.processes.sort(key=lambda x: x['start_order'])
            for i, process in enumerate(self.processes):
                if not process['started']:
                    process['shutdown_order'] = len(self.processes) - i
                    for j in range(process['num_processes']):
                        p = mp.Process(target=process['target'], 
                                    name=f'{process['name']}_{j}',
                                    args=process['args'],
                        )
                        process['processes'].append(p)
                        p.start()
                        self.logger.debug(f'Started process {p.name} (PID={p.pid}).')
                    process['started'] = True
        except KeyboardInterrupt:
            self.terminate_all_processes()

    def wait_for_processes(self):
        try:
            self.processes.sort(key=lambda x: x['shutdown_order'],)
            for process in self.processes:
                for p in process['processes']:
                    self.logger.debug(f'Joining process {p.name} (PID={p.pid}).')
                    p.join()
        except KeyboardInterrupt:
            self.terminate_all_processes()

    def terminate_all_processes(self):
        self.logger.debug(f'{self.name}: Terminating all processes.')
        self.processes.sort(key=lambda x: x['shutdown_order'],)
        for process in self.processes:
            for p in process['processes']:
                self.logger.debug(f'Terminating process {p.name} (PID={p.pid}).')
                p.terminate()
                p.join()        

def _isAll(x) -> bool:
    '''
    Checks if the provided content is equal to the string 'all'.  If it's a list or a dictionary, it will compare only the first item.

    Returns None if it's one of those three types.
    '''

    s=''
    if isinstance(x, list):
        s = x[0]
    if isinstance(x, str):
        s= x
    if isinstance(x, dict):
        s = list(x.keys())[0]
    
    return s.lower() == 'all'


if __name__=="__main__":
    config=Config('piidigger.toml')

    print(config.getConfig())