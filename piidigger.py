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

# Embedded Python skips site, so pywin32.pth never runs its bootstrap.  Without it, win32api
# can't find the pywin32_system32 DLLs and placeholder-file detection in _enum_dir silently
# turns off.  Module level so spawned worker processes get it too.
if sys.platform == "win32":
    import pywin32_bootstrap  # noqa: F401

if __name__ == "__main__":
    freeze_support()
    from piidigger.cli.main import cli

    cli()
