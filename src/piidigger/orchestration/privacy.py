"""The macOS privacy probe: folders that macOS privacy controls refuse to list.

Without Full Disk Access, macOS refuses folders such as ~/Library/Mail with
EPERM ("Operation not permitted").  An ordinary permission refusal is EACCES
("Permission denied"), which granting Full Disk Access does not fix, so only
EPERM counts.  Listing a few likely folders before the workers start lets the
user grant access now, not hours into the scan.  It also brings up macOS's own
Desktop, Documents and Downloads dialogs at the start.

The probe does not read TCC.db, the privacy database.  Apple does not document
it, and its location has changed between releases.
"""

from __future__ import annotations

import contextlib
import errno
import itertools
import os
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from piidigger.orchestration.worker import is_excluded_dir

# The most folders the probe lists.  A home folder's grandchildren can number
# in the thousands, and the probe only needs a few blocked ones to be useful.
# A listing of a local folder takes well under a millisecond.
PROBE_LISTING_LIMIT: int = 1000

# The longest the probe keeps starting listings, in seconds.  It runs before
# the progress display, so a slow network folder must not hold up the start.
# Checked before each listing.  A listing that hangs is not interrupted: the
# caller waits in another thread (see run._probe_privacy()).
PROBE_TIME_LIMIT: float = 5.0


@dataclass(frozen=True, slots=True)
class PrivacyProbe:
    """What the probe found.

    A dataclass, not a Pydantic model: every field is a value the probe built.
    blocked lists the refused folders in the order the probe reached them.
    capped is True when the listing or time limit stopped the probe early.
    """

    blocked: tuple[Path, ...]
    listed: int
    capped: bool


def probe_privacy_blocked(
    start_dirs: Sequence[Path],
    exclude_dirs: list[str],
    *,
    home: Path | None = None,
    limit: int = PROBE_LISTING_LIMIT,
    time_limit: float = PROBE_TIME_LIMIT,
) -> PrivacyProbe:
    """List each start folder and its subfolders, counting the ones macOS privacy controls refuse.

    When home is inside a start folder, home's subfolders and theirs are listed
    too, since that is where macOS protects folders.  No Apple folder names are
    built in.  Folders the scan would skip are skipped here too: symlinks and
    exclude_dirs entries (already resolved, see resolve_exclude_dirs()).

    Home goes first: home, its subfolders, then theirs, then the start folders'
    other subfolders.  So a start folder with thousands of subfolders (/ or
    /Users) cannot use up the limit before ~/Library/Mail is reached.  At
    home's grandchildren the probe takes one from each subfolder in turn, so a
    large Documents folder cannot use it up before ~/Library's either.
    """
    blocked: list[Path] = []
    seen: set[Path] = set()  # real paths, so a folder reached by two spellings is listed once
    listed = 0
    deadline = time.monotonic() + time_limit

    def refused(folder: Path, exc: PermissionError) -> None:
        if exc.errno == errno.EPERM:  # once per folder: seen stops a second visit
            blocked.append(folder)

    def visit(folder: Path) -> list[Path] | None:
        """List folder once: its subfolders, or None if it was not listed."""
        nonlocal listed
        real = _real(folder)
        if real in seen or is_excluded_dir(folder, exclude_dirs):
            return None
        seen.add(real)
        listed += 1
        try:
            entries = os.scandir(folder)
        except PermissionError as exc:
            refused(folder, exc)
            return None
        except OSError:
            return None  # gone, or not a folder: the scan reports it
        # An error partway through keeps the subfolders found before it, so
        # one unreadable entry cannot hide a blocked folder beside it.
        subfolders: list[Path] = []
        with entries:
            try:
                for entry in entries:
                    with contextlib.suppress(OSError):
                        if entry.is_dir(follow_symlinks=False):
                            subfolders.append(Path(entry.path))
            except PermissionError as exc:
                refused(folder, exc)
            except OSError:
                pass
        return subfolders

    def visit_all(folders: Iterable[Path]) -> tuple[list[list[Path]], bool]:
        """Visit each folder until a limit: the listed folders' subfolders, and whether a limit was hit."""
        listings: list[list[Path]] = []
        for folder in folders:
            if listed >= limit or time.monotonic() >= deadline:
                return listings, True
            if (subfolders := visit(folder)) is not None:
                listings.append(subfolders)
        return listings, False

    under_home: list[list[Path]] = []
    capped = False
    if home is not None and any(_is_inside(home, start) for start in start_dirs):
        listing, capped = visit_all([home])
        if listing and not capped:
            under_home, capped = visit_all(listing[0])
            if not capped:
                _, capped = visit_all(_round_robin(under_home))
    if not capped:
        first, capped = visit_all(start_dirs)
        if not capped:
            _, capped = visit_all(itertools.chain.from_iterable(first))
    return PrivacyProbe(blocked=tuple(blocked), listed=listed, capped=capped)


def _real(path: Path) -> Path:
    """path's real path, or path itself when it cannot be resolved."""
    try:
        return path.resolve()
    except OSError:
        return path


def _is_inside(path: Path, folder: Path) -> bool:
    """True when path is folder or below it, compared by real path."""
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def _round_robin(lists: list[list[Path]]) -> list[Path]:
    """Take one from each list in turn, until all are used up."""
    interleaved = itertools.chain.from_iterable(itertools.zip_longest(*lists))
    return [path for path in interleaved if path is not None]
