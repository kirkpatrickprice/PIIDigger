"""Tests for orchestration/sleep.py — wakepy first, then a logind lock on Linux."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from piidigger.orchestration import sleep as sleep_mod
from piidigger.orchestration.sleep import SleepStatus, _take_logind_lock, prevent_sleep

_LOGGER = logging.getLogger("tests.sleep")


def _set_wakepy(monkeypatch: pytest.MonkeyPatch, *, active: bool) -> None:
    @contextmanager
    def running(on_fail: str) -> Any:
        assert on_fail == "pass"
        yield SimpleNamespace(active=active, result=SimpleNamespace(get_failure_text=lambda style: f"wakepy {style}"))

    monkeypatch.setattr(sleep_mod.keep, "running", running)


def _fail_if_called() -> int:
    raise AssertionError("logind must not be tried")


def _is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def test_wakepy_active_never_tries_logind(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_wakepy(monkeypatch, active=True)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", _fail_if_called)
    monkeypatch.setattr(sleep_mod.sys, "platform", "linux")

    with prevent_sleep(_LOGGER) as status:
        assert status is SleepStatus.ACTIVE


def test_other_platforms_never_try_logind(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", _fail_if_called)
    monkeypatch.setattr(sleep_mod.sys, "platform", "win32")

    with prevent_sleep(_LOGGER) as status:
        assert status is SleepStatus.UNAVAILABLE


def test_linux_falls_back_to_logind_and_releases_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_fd = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR)
    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", lambda: lock_fd)
    monkeypatch.setattr(sleep_mod.sys, "platform", "linux")

    with prevent_sleep(_LOGGER) as status:
        assert status is SleepStatus.ACTIVE_LOGIND
        assert _is_open(lock_fd)
    assert not _is_open(lock_fd)


def test_logind_lock_is_released_when_the_scan_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_fd = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR)
    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", lambda: lock_fd)
    monkeypatch.setattr(sleep_mod.sys, "platform", "linux")

    with pytest.raises(KeyboardInterrupt), prevent_sleep(_LOGGER):
        raise KeyboardInterrupt
    assert not _is_open(lock_fd)


def test_linux_without_logind_reports_unavailable_and_logs_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def no_bus() -> int:
        raise FileNotFoundError("no system bus socket")

    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", no_bus)
    monkeypatch.setattr(sleep_mod.sys, "platform", "linux")

    with caplog.at_level(logging.INFO, logger=_LOGGER.name), prevent_sleep(_LOGGER) as status:
        assert status is SleepStatus.UNAVAILABLE_LINUX
    assert "no system bus socket" in caplog.text
    assert "wakepy inline" in caplog.text


def test_other_platforms_log_why_wakepy_failed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod.sys, "platform", "win32")

    with caplog.at_level(logging.INFO, logger=_LOGGER.name), prevent_sleep(_LOGGER):
        pass
    assert "wakepy inline" in caplog.text


def test_wakepy_failure_is_only_a_diagnostic_once_logind_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lock_fd = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR)
    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", lambda: lock_fd)
    monkeypatch.setattr(sleep_mod.sys, "platform", "linux")

    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name), prevent_sleep(_LOGGER) as status:
        assert status is SleepStatus.ACTIVE_LOGIND
    assert [r.levelno for r in caplog.records if "wakepy inline" in r.getMessage()] == [logging.DEBUG]


def test_a_scan_failure_is_not_mistaken_for_a_failed_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_bus() -> int:
        raise FileNotFoundError("no system bus socket")

    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", no_bus)
    monkeypatch.setattr(sleep_mod.sys, "platform", "linux")

    with pytest.raises(ValueError, match="from the scan") as raised, prevent_sleep(_LOGGER):
        raise ValueError("from the scan")
    assert raised.value.__context__ is None, "the bus error must not lead the scan's traceback"


class _ExitFails:
    """A wakepy mode whose request cannot be released, as when the session ended."""

    def __enter__(self) -> SimpleNamespace:
        return SimpleNamespace(active=True)

    def __exit__(self, *_exc: object) -> bool:
        raise RuntimeError("session bus gone")


def test_failure_leaving_wakepy_does_not_crash_a_finished_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sleep_mod.keep, "running", lambda on_fail: _ExitFails())

    with prevent_sleep(_LOGGER) as status:
        assert status is SleepStatus.ACTIVE


def test_failure_leaving_wakepy_does_not_hide_a_scan_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sleep_mod.keep, "running", lambda on_fail: _ExitFails())

    with pytest.raises(ValueError, match="from the scan"), prevent_sleep(_LOGGER):
        raise ValueError("from the scan")


def test_wedged_bus_does_not_hold_up_the_scan_and_a_late_lock_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_fd = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR)
    release = threading.Event()

    def wedged() -> int:
        release.wait(timeout=30)
        return lock_fd

    _set_wakepy(monkeypatch, active=False)
    monkeypatch.setattr(sleep_mod, "_take_logind_lock", wedged)
    monkeypatch.setattr(sleep_mod, "_LOGIND_REPLY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(sleep_mod.sys, "platform", "linux")

    with prevent_sleep(_LOGGER) as status:
        assert status is SleepStatus.UNAVAILABLE_LINUX
    assert _is_open(lock_fd), "the attempt is still running"

    release.set()
    deadline = time.monotonic() + 5
    while _is_open(lock_fd) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _is_open(lock_fd)


class _FakeConnection:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.sent: Any = None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def send_and_get_reply(self, message: Any, *, timeout: float) -> Any:
        assert timeout > 0, "a wedged bus must not hold up the scan"
        self.sent = message
        return self.reply


def test_take_logind_lock_asks_for_a_blocking_lock_workers_cannot_inherit(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("jeepney")
    from jeepney import HeaderFields, MessageType, new_method_return
    from jeepney.fds import FileDescriptor
    from jeepney.low_level import Header, Message

    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    method_call = Message(Header(None, MessageType.method_call, 0, 1, None, 1, {}), ())
    reply = new_method_return(method_call, "h", (FileDescriptor(read_fd),))
    connection = _FakeConnection(reply)
    buses: list[tuple[str, bool]] = []

    def fake_open(bus: str, enable_fds: bool) -> _FakeConnection:
        buses.append((bus, enable_fds))
        return connection

    monkeypatch.setattr("jeepney.io.blocking.open_dbus_connection", fake_open)
    try:
        lock_fd = _take_logind_lock()
        assert lock_fd == read_fd
        assert not os.get_inheritable(lock_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    # The system bus, because sudo removes the session bus.
    assert buses == [("SYSTEM", True)]
    assert connection.sent.header.fields[HeaderFields.member] == "Inhibit"
    assert connection.sent.body == ("sleep:idle", "PIIDigger", "Scan in progress", "block")


def test_take_logind_lock_raises_when_logind_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("jeepney")
    from jeepney import DBusErrorResponse, MessageType
    from jeepney.low_level import Header, Message

    refusal = Message(
        Header(None, MessageType.error, 0, 1, None, 1, {}),
        ("Access denied",),
    )
    monkeypatch.setattr("jeepney.io.blocking.open_dbus_connection", lambda _bus, enable_fds: _FakeConnection(refusal))

    with pytest.raises(DBusErrorResponse):
        _take_logind_lock()
