"""Tests for the macOS privacy probe (orchestration/privacy.py).

Real folders under tmp_path, with os.scandir faked to refuse chosen ones the
way macOS does: EPERM for privacy protection, EACCES for ordinary permissions.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from piidigger.orchestration import privacy
from piidigger.orchestration.privacy import probe_privacy_blocked


def _make(root: Path, *relative: str) -> None:
    for name in relative:
        (root / name).mkdir(parents=True)


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[list[Path], dict[Path, int]]]:
    """Record each folder the probe lists; refuse the folders put in the dict with that errno."""
    calls: list[Path] = []
    refuse: dict[Path, int] = {}
    real_scandir = os.scandir

    def fake_scandir(path: Path) -> object:
        calls.append(Path(path))
        if (code := refuse.get(Path(path))) is not None:
            raise PermissionError(code, os.strerror(code), str(path))
        return real_scandir(path)

    monkeypatch.setattr(privacy.os, "scandir", fake_scandir)
    yield calls, refuse


@pytest.mark.unit
def test_counts_privacy_refusals_but_not_ordinary_permission_refusals(
    tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]
) -> None:
    """Full Disk Access fixes EPERM, not EACCES, so only EPERM is worth the prompt."""
    _, refuse = listed
    _make(tmp_path, "Mail", "locked", "open")
    refuse[tmp_path / "Mail"] = errno.EPERM
    refuse[tmp_path / "locked"] = errno.EACCES

    probe = probe_privacy_blocked([tmp_path], [])

    assert probe.blocked == (tmp_path / "Mail",)
    assert probe.listed == 4
    assert not probe.capped


@pytest.mark.unit
def test_lists_home_two_levels_down_when_a_start_folder_holds_it(
    tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]
) -> None:
    """A start folder's grandchildren are not listed, but home's are: macOS protects ~/Library/Mail."""
    calls, refuse = listed
    home = tmp_path / "Users" / "x"
    _make(tmp_path, "Users/x/Library/Mail", "Users/x/Library/Mail/deep", "other/grandchild")
    refuse[home / "Library" / "Mail"] = errno.EPERM

    probe = probe_privacy_blocked([tmp_path], [], home=home)

    assert probe.blocked == (home / "Library" / "Mail",)
    assert tmp_path / "other" / "grandchild" not in calls, "only home goes deeper than one level"
    assert home / "Library" / "Mail" / "deep" not in calls, "home goes two levels down, no further"
    assert len(calls) == len(set(calls)), "each folder is listed once"


@pytest.mark.unit
def test_home_outside_every_start_folder_is_not_listed(
    tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]
) -> None:
    calls, _ = listed
    _make(tmp_path, "scan/sub", "home/Library")

    probe_privacy_blocked([tmp_path / "scan"], [], home=tmp_path / "home")

    assert calls == [tmp_path / "scan", tmp_path / "scan" / "sub"]


@pytest.mark.unit
def test_skips_what_the_scan_skips(tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]) -> None:
    """A refused folder the scan never reaches must not prompt the user."""
    calls, refuse = listed
    _make(tmp_path, "excluded", "real")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    refuse[tmp_path / "excluded"] = errno.EPERM

    probe = probe_privacy_blocked([tmp_path], [str(tmp_path / "excluded")])

    assert probe.blocked == ()
    assert calls == [tmp_path, tmp_path / "real"]


@pytest.mark.unit
def test_stops_at_the_limit_and_says_so(tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]) -> None:
    calls, _ = listed
    _make(tmp_path, *(f"d{n}" for n in range(10)))

    probe = probe_privacy_blocked([tmp_path], [], limit=4)

    assert len(calls) == probe.listed == 4
    assert probe.capped


@pytest.mark.unit
def test_exactly_the_limit_is_not_capped(tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]) -> None:
    _make(tmp_path, "a", "b")

    probe = probe_privacy_blocked([tmp_path], [], limit=3)

    assert (probe.listed, probe.capped) == (3, False)


@pytest.mark.unit
def test_home_grandchildren_are_taken_from_each_subfolder_in_turn(
    tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]
) -> None:
    """A Documents folder with many subfolders must not use up the limit before ~/Library's are reached.

    Every grandchild is refused, so the blocked list shows which ones were
    reached, whatever order os.scandir lists them in.
    """
    _, refuse = listed
    home = tmp_path
    for parent in ("Documents", "Library"):
        _make(home, *(f"{parent}/p{n:02}" for n in range(20)))
        refuse.update({home / parent / f"p{n:02}": errno.EPERM for n in range(20)})

    # home, Documents, Library, then 2 grandchildren: one from each.
    probe = probe_privacy_blocked([home], [], home=home, limit=5)

    assert {folder.parent.name for folder in probe.blocked} == {"Documents", "Library"}
    assert probe.capped


@pytest.mark.unit
def test_a_refused_start_folder_counts(tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]) -> None:
    _, refuse = listed
    refuse[tmp_path] = errno.EPERM

    assert probe_privacy_blocked([tmp_path], []).blocked == (tmp_path,)


@pytest.mark.unit
def test_a_missing_start_folder_is_left_to_the_scan(tmp_path: Path) -> None:
    probe = probe_privacy_blocked([tmp_path / "gone"], [])

    assert (probe.blocked, probe.listed) == ((), 1)


@pytest.mark.unit
def test_lists_a_folder_reached_by_two_spellings_once_and_still_goes_under_home(
    tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]
) -> None:
    """A start folder that is home through a symlink must not double the count, nor skip home's grandchildren."""
    calls, refuse = listed
    real_home = tmp_path / "real_home"
    _make(real_home, "Library/Mail")
    alias = tmp_path / "alias"
    alias.symlink_to(real_home, target_is_directory=True)
    refuse[alias / "Library" / "Mail"] = errno.EPERM
    refuse[real_home / "Library" / "Mail"] = errno.EPERM

    probe = probe_privacy_blocked([alias], [], home=real_home)

    assert len(probe.blocked) == 1
    assert probe.blocked[0].name == "Mail"
    assert probe.listed == 3, "home, Library, Mail: each once"


@pytest.mark.unit
def test_an_entry_that_fails_partway_keeps_the_subfolders_found_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unreadable entry must not hide a blocked folder listed before it."""
    _make(tmp_path, "Mail")
    real_scandir = os.scandir

    class _Breaks:
        """A listing that yields its real entries, then fails."""

        def __init__(self, path: Path) -> None:
            self._inner = real_scandir(path)

        def __enter__(self) -> _Breaks:
            return self

        def __exit__(self, *_exc: object) -> None:
            self._inner.close()

        def __iter__(self) -> Iterator[os.DirEntry[str]]:
            yield from self._inner
            raise OSError(errno.EIO, "I/O error")

    def fake_scandir(path: Path) -> object:
        if Path(path) == tmp_path / "Mail":
            raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), str(path))
        return _Breaks(path) if Path(path) == tmp_path else real_scandir(path)

    monkeypatch.setattr(privacy.os, "scandir", fake_scandir)

    assert probe_privacy_blocked([tmp_path], []).blocked == (tmp_path / "Mail",)


@pytest.mark.unit
def test_home_comes_before_a_start_folder_with_many_subfolders(
    tmp_path: Path, listed: tuple[list[Path], dict[Path, int]]
) -> None:
    """A start folder like / or /Users must not use up the limit before ~/Library/Mail is reached."""
    _, refuse = listed
    home = tmp_path / "Users" / "x"
    _make(tmp_path, *(f"Users/other{n:02}" for n in range(30)), "Users/x/Library/Mail")
    refuse[home / "Library" / "Mail"] = errno.EPERM

    # home, Library, Mail: three listings reach it, whatever else /Users holds.
    probe = probe_privacy_blocked([tmp_path / "Users"], [], home=home, limit=3)

    assert probe.blocked == (home / "Library" / "Mail",)


@pytest.mark.unit
def test_a_refusal_while_reading_a_listing_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_scandir = os.scandir

    class _RefusedPartway:
        def __enter__(self) -> _RefusedPartway:
            return self

        def __exit__(self, *_exc: object) -> None:
            pass

        def __iter__(self) -> Iterator[os.DirEntry[str]]:
            raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), str(tmp_path))

    monkeypatch.setattr(
        privacy.os, "scandir", lambda path: _RefusedPartway() if Path(path) == tmp_path else real_scandir(path)
    )

    assert probe_privacy_blocked([tmp_path], []).blocked == (tmp_path,)


@pytest.mark.unit
def test_stops_at_the_time_limit_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow network folder must not hold up the start of the scan."""
    _make(tmp_path, *(f"d{n}" for n in range(10)))
    clock = iter(range(100))
    monkeypatch.setattr(privacy.time, "monotonic", lambda: float(next(clock)))

    # The deadline is read at 0, then each listing starts one second later.
    probe = probe_privacy_blocked([tmp_path], [], time_limit=3.5)

    assert (probe.listed, probe.capped) == (3, True)
