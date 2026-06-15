"""Console output helpers backed by Rich.

This module replaces the previous hand-rolled cross-platform terminal code
(colorama + ctypes/ioctl/tput terminal sizing).  Rich handles ANSI colour,
Windows consoles, terminal width detection, and graceful degradation when
output is not a TTY (e.g. piped to a file or running in CI).

Public API is unchanged: ``normal``, ``warn``, ``error``, ``status`` and
``getTerminalSize``.

Stream convention: informational/list output goes to stdout so it can be piped;
warnings, errors and the live progress line go to stderr so they never pollute
piped stdout.

The live ``status`` line stays a simple carriage-return write for now.  Wiring a
multiprocess-safe ``rich.progress`` display owned by the scan coordinator is part
of the task-queue refactor, not this module.
"""

import sys

from rich.console import Console

# Two consoles so data (stdout) and diagnostics/progress (stderr) stay separate.
_out = Console(highlight=False)
_err = Console(stderr=True, highlight=False)

__all__ = ['getTerminalSize', 'warn', 'error', 'normal', 'status']


def getTerminalSize() -> tuple[int, int]:
    '''Return the terminal as a (width, height) tuple (Rich falls back to 80x25).'''
    size = _err.size
    return (size.width, size.height)


def normal(s: str) -> None:
    '''Print an informational message to stdout.'''
    _out.print(s, markup=False, highlight=False)


def status(s: str) -> None:
    '''Write an in-place (carriage-return) progress line to stderr.

    Skipped when stderr is not a terminal so redirected logs are not spammed
    with partial carriage-return updates.
    '''
    if not _err.is_terminal:
        return
    sys.stderr.write('\r' + s)
    sys.stderr.flush()


def warn(s: str) -> None:
    '''Print a warning (prefixed and yellow) to stderr.'''
    _err.print(f'[warn] {s}', style='yellow', markup=False, highlight=False)


def error(s: str) -> None:
    '''Print an error (prefixed and red) to stderr.'''
    _err.print(f'[error] {s}', style='red', markup=False, highlight=False)


if __name__ == "__main__":
    sizex, sizey = getTerminalSize()
    normal(f'width = {sizex} height = {sizey}')
    error('This is error text')
    warn('This is warning text')
    normal('This is normal text')
