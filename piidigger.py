#!/usr/bin/env python
'''src/piidigger/piidigger.py wrapper'''

import sys
import traceback
from multiprocessing import Process, freeze_support
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parent / "src"))

from piidigger.globalvars import errorCodes
from piidigger.piidigger import main

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
    