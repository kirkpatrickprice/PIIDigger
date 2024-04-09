import sys
import traceback
from multiprocessing import Process, freeze_support, set_start_method

from piidigger.piidigger import main
from piidigger.globalvars import errorCodes

exitCode = errorCodes['ok']

if __name__=='__main__':
    freeze_support()
    try:
        m = Process(target=main)
        m.start()
        m.join()
        exitCode = m.exitcode
    except KeyboardInterrupt:
        pass
    except Exception:
        exitCode = errorCodes['unknownError']
        errorFile='piidigger.exc'
        print(f'An unknown error was encountered.  Detailed error information has been written to {errorFile}.')
        traceback.print_exception(file=errorFile)
    sys.exit(exitCode)