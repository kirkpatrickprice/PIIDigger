"""Filesystem helpers shared by tests that need path aliases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def make_dir_alias(alias: Path, target: Path) -> None:
    """Make alias a second route to the directory target, or skip the test.

    Tries a symlink first.  On Windows, creating one needs Developer Mode or
    admin rights, so fall back to a directory junction, which needs neither
    and which Path.resolve() / os.path.realpath() also follow.
    """
    try:
        alias.symlink_to(target, target_is_directory=True)
        return
    except OSError, NotImplementedError:
        pass
    if sys.platform == "win32":
        import _winapi  # noqa: PLC0415

        try:
            _winapi.CreateJunction(str(target), str(alias))
            return
        except OSError:
            pass
    pytest.skip("cannot create a directory alias on this platform")
