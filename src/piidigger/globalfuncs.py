from multiprocessing import Queue
from queue import Empty
import os
import ctypes
import platform
from time import sleep


from piidigger import console
from piidigger import filehandlers as fh
from piidigger import datahandlers as dh
from piidigger import outputhandlers as oh
from piidigger.globalvars import maxChunkSize


# Dynamically build the supported file handlers based on the contents of the filehandlers package.
# Each file handler needs a globally-defined variable called "handles" with a dictionary as follows:
#   ext: [a list of file extensions with the leading .]
#   mime: [a list of mime-type strings]

fileHandlers={ handler: getattr(getattr(fh, handler), 'handles') for handler in fh.__dir__() if not handler.startswith('_') }

# Leave these in here as reference until I write more file handlers.
# fileHandlers={
#     'archive': {
#         'ext': ['.7z','.bz2','.gz','.gzip','.tar','.zip',],
#         'mime': ['application/x-7z-compressed','application/gzip','application/zip','application/x-tar','application/x-bzip2',],
#     },
#     'appledocs': {
#         'ext': ['.numbers',],
#         'mime': [],
#     },
#     'csv': {
#         'ext': ['.csv',],
#         'mime': ['text/csv'],
#     },
#     'googledocs': {
#         'ext': ['.gsheet',],
#         'mime': [],
#     },
#     'msoffice': {
#         'ext': ['.doc','.docx','.ppt','.pptx','.xls','.xlsx',],
#         'mime': [
#             'application/msword',
#             'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
#             'application/vnd.ms-powerpoint',
#             'application/vnd.openxmlformats-officedocument.presentationml.presentation',
#             'application/vnd.ms-excel',
#             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
#         ],
#     },
#     'openoffice': {
#         'ext': ['.odp','.ods','.odt','.odp',],
#         'mime': ['application/vnd.oasis.opendocument.spreadsheet', 'application/vnd.oasis.opendocument.text', 'application/vnd.oasis.opendocument.presentation'],
#     },
#     'outlook': {
#         'ext': ['.pst','.ost',],
#         'mime': [],
#     },
#     'pdf': {
#         'ext': ['.pdf',],
#         'mime': ['application/pdf',],
#     },
#     'richtext': {
#         'ext': ['.rtf',],
#     	'mime': ['text/rtf'],
#     },    
# }


######################################################
############### Global functions #####################
######################################################


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
    

def countResults(results: dict) -> int:
    '''Receives a dictionary of results and returns the total match count across all match sets in the dictionary'''

    count=0
    for key in results.keys():
        item=results[key]
        if isinstance(item, dict):
            count+=countResults(item)
        elif isinstance(item, list):
            count+=len(item)
    
    return count


def getAllDataHandlerModules() -> list:
    '''
    Returns a list containing the data handler modules from datahandler package
    '''
    
    return [getattr(dh, module) for module in getSupportedDataHandlerNames()]


def getDataHandlerModule(name: str):
    '''
    Returns a module object for a specific data handler
    '''
    try:
        return getattr(dh, name)
    except Exception:
        return None


def getDefaultConfig() -> dict:
    '''
    Returns a default configuration object for when a configuration file couldn't be found.
    '''

    return {
        'dataHandlers': [
            'pan',
        ],
        'localFilesOnly': True,
        'results': {'path': "piidigger-results/",
                    'json': True,
                    'text': True,}, 
        'includeFiles': {'ext': 'all', 
                            'mime': 'all', 
                            'startDirs': {'windows': 'all', 
                                        'linux': ['/'], 
                                        'darwin': ['/']}},
        'excludeDirs': {
            'windows': ['C:\\Windows', 'C:\\Program Files (x86)', 'C:\\Program Files',], 
            'linux': ['/boot', '/dev', '/etc', '/proc', '/run', '/snap', '/sys', '/usr/bin', '/usr/lib', '/usr/lib32', '/usr/lib64', '/usr/libx32', '/usr/local', '/usr/sbin', '/usr/share', '/usr/src/', '*/.vscode-server', '/mnt/c', '/mnt/d', '/mnt/wslg', '/wsl'],
            'darwin': ["/dev", '/etc', '/usr/bin', '/usr/local/Homebrew', '/usr/lib', '/usr/sbin', '/Applications', '/Library/Developer', '/Library/Documentation', '/System',]},
        'logging': {'logLevel': 'INFO', 
                    'logFile': 'logs/piidigger.log'}
        }


def getEnabledDataHandlerModules(moduleNames: list):
    '''
    Receives a list of enabled module names and returns a list of the module objects
    '''
    return [getDataHandlerModule(name) for name in moduleNames]


def getFileHandlerName(ext: str, mime: str) -> str:
    '''
    Receives a file extension and MIME type

    Returns a file handler to use to work with the file or None

    All file handlers should be in the "handler" module/directory in a Python file by the name of handler
    '''

    handler=None

    for key in fileHandlers.keys():
        if (ext in fileHandlers[key]['ext']) or (mime in fileHandlers[key]['mime']):
            handler=str(key)
            break
    
    return handler


def getFileHandlerModule(name):
    '''
    Returns a module object for a file handler by the provided name
    '''    
    try:
        return getattr(fh, name)
    except Exception:
        return None


def getOutputHandlerModule(name: str):
    '''
    Returns a module object for a specific output handler
    '''

    try:
        return getattr(oh, name)
    except Exception:
        return None    


def getOSType() -> str:
    return platform.system().lower()


def getSupportedDataHandlerNames() -> list:
    return [n for n in dh.__dir__() if not n.startswith("__")]


def getSupportedFileExts() -> list:
    '''
    Returns a list of all supported file extentions
    '''

    exts=[]

    for key in fileHandlers.keys():
        exts+=fileHandlers[key]['ext']

    return exts


def getSupportedFileMimes() -> list:
    '''
    Returns a list of all supported file extentions
    '''

    mimes=[]

    for key in fileHandlers.keys():
        mimes+=fileHandlers[key]['mime']

    return mimes


def isAdmin() -> bool:
    try:
        check = (os.geteuid() == 0)
    except AttributeError:
        check = ctypes.windll.shell32.IsUserAnAdmin() != 0
    return check


def makeChunks(s: str, chunkSize: int=maxChunkSize) -> list:
    '''Breaks up a string into smaller strings not larger than chunkSize'''

    words = s.split()
    wordNum = 0
    chunks=list()

    while wordNum<len(words):
        chunk=str()
        while len(chunk) < chunkSize and wordNum < len(words):
            if len(words[wordNum]) > chunkSize:
                # Break up super-long strings into shorter words and add them to the word list
                chunkList = [words[wordNum][i:i+chunkSize] for i in range(0, len(words[wordNum]), chunkSize)]
                words = [*words, *chunkList]
            else:
                # Add the next word to the current chunk
                chunk+=words[wordNum] + ' '
            wordNum+=1
            
        # Add the current chunk to the list of chunks
        chunks+=[chunk.strip()]

    return chunks


def processMatches(results: dict, 
                   matches: dict, 
                   dhName: str) -> dict:
    
    '''Process the results from RE matches and add them to the results dictionary'''

    for key in matches.keys():
        value = matches[key]
        if value:
            if not dhName in results['matches']:
                results['matches'][dhName]=dict()
            if not key in results['matches'][dhName]:
                results['matches'][dhName][key]=set()
            results['matches'][dhName][key].update(value)

    return results


def progressLine(*pargs, **kwargs):
    '''
    Prints a status line that includes details about directories, files, and results
    '''
    screenWidth=console.getTerminalSize()[0]

    line='Folders scanned: %d | Files identified: %d | Files scanned: %d | Results found: %d' % (kwargs['totalDirs'].value, kwargs['totalFilesFound'].value, kwargs['totalFilesScanned'].value, kwargs['totalResults'].value)

    if len(line) > screenWidth:
        line=line[:screenWidth-1]

    console.status(line)


def sizeof_fmt(num, suffix="B"):
    '''
    Returns a human-readable string for bytes.
    '''

    # Taken from https://stackoverflow.com/questions/1094841/get-human-readable-version-of-file-size
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Y{suffix}"


def writeDefaultConfig(tomlFile: str):
    # This is a total kluge, but without a reasonable Python library to write a TOML v1.1 file based on a Python dictionary, we have to build the default config file from scratch
    
    def _tomlfy(key, value):
        if isinstance(value, str):
            return key + ' = "' + value + '"'
        elif isinstance(value, bool):
            return key + ' = ' + str(value).lower()
        else:
            return key + ' = ' + str(value).replace('\'', '"')
    
    defaultConfig=getDefaultConfig()
    lines = list()
    for key in ['dataHandlers',]:
        lines.append(_tomlfy(key, defaultConfig[key]))

    lines.append('')
    for key in ['localFilesOnly']:
        lines.append(_tomlfy(key, defaultConfig[key]))
    
    lines.append('')
    lines.append('[results]')
    for key in ['path', 'json', 'text']:
        lines.append(_tomlfy(key, defaultConfig['results'][key]))
        
    lines.append('')
    lines.append('[includeFiles]')
    for key in ['ext', 'mime']:
        lines.append(_tomlfy(key,defaultConfig['includeFiles'][key]))
            
    lines.append('')
    lines.append('[includeFiles.startDirs]')
    for key in ['windows', 'linux', 'darwin']:
        lines.append(_tomlfy(key, defaultConfig['includeFiles']['startDirs'][key]))
        # if isinstance(value, str):
        #     lines.append(pf + ' = "' + value + '"')
        # else:
        #     lines.append(pf + ' = ' + str(defaultConfig['includeFiles']['startDirs'][pf]).replace('\'', '"'))
    
    lines.append('')
    lines.append('[excludeDirs]')
    for key in ['windows', 'linux', 'darwin']:
        lines.append(_tomlfy(key, defaultConfig['excludeDirs'][key]))
    
    lines.append('')
    lines.append('[logging]')
    for key in ['logLevel', 'logFile']:
        lines.append(_tomlfy(key, defaultConfig['logging'][key]))
    
    try:
        with open(tomlFile, 'w') as tf:
            tf.writelines(line + '\n' for line in lines)
        return "Success"
    except Exception as e:
        return str(e)

