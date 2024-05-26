#!/usr/bin/env python
'''src/piidigger/piidigger.py wrapper'''

import sys
import traceback
from pathlib import Path
from multiprocessing import Process, freeze_support, set_start_method

sys.path.insert(0, str(Path(__file__).absolute().parent / "src"))

from piidigger.piidigger import main
from piidigger.globalvars import errorCodes

exitCode = errorCodes['ok']

if __name__=='__main__':
    try:
        freeze_support()
        m = Process(target=main)
        m.start()
        m.join()
        exitCode = m.exitcode
    except KeyboardInterrupt:
        pass
    except Exception:
        exit_code = errorCodes['unknownError']
        errorFile='piidigger.exc'
        print(f'An unknown error was encountered.  Detailed error information has been written to {errorFile}.')
        traceback.print_exception(file=errorFile)
    sys.exit(exitCode)
    