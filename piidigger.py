#!/usr/bin/env python
"""Entry point for the embedded-Python Windows launcher (piidigger.cmd).

sys.path is extended to include src/ so that the piidigger package is
importable when running via bin/python.exe without a full installation.
freeze_support() must be called before any multiprocessing on Windows
when the interpreter is a standalone/frozen binary.
"""

import sys
from multiprocessing import freeze_support
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parent / "src"))

if __name__ == "__main__":
    freeze_support()
    from piidigger.cli.main import cli

    cli()
