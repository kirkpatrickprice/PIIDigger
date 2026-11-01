"""Keep the machine awake for the length of a scan.

wakepy goes first, on every platform.  Its Linux methods need the desktop's
session bus, which sudo removes.  They also check the Plasma version by
running plasmashell, which dies inside a PyInstaller bundle because it inherits
the bundle's older libgcc.  So on Linux a failed wakepy falls back to logind,
which needs only the system bus and starts no subprocess.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager, suppress
from enum import StrEnum
from typing import Any

from wakepy import keep

# logind normally answers in milliseconds.  A wedged system bus must not hold
# up a scan, so the scan starts without the lock after this long.  Used for the
# whole attempt too: jeepney's opening Hello call takes no timeout.
_LOGIND_REPLY_TIMEOUT_SECONDS: float = 5.0


class SleepStatus(StrEnum):
    """What the startup summary reports; the value is the text shown."""

    ACTIVE = "Active"
    ACTIVE_LOGIND = "Active (logind)"
    UNAVAILABLE = "Unavailable"
    UNAVAILABLE_LINUX = "Unavailable (no desktop session or logind lock)"


def _take_logind_lock() -> int:
    """Take a sleep and idle inhibitor lock from logind; return its file descriptor.

    The lock lasts until every copy of the descriptor is closed, so the kernel
    releases it when this process exits, even after a crash.  Closing the
    D-Bus connection does not release it.  Raises if the lock cannot be taken.
    """
    # Imported here because jeepney is only installed on Linux.
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import unwrap_msg

    manager = DBusAddress(
        "/org/freedesktop/login1",
        bus_name="org.freedesktop.login1",
        interface="org.freedesktop.login1.Manager",
    )
    message = new_method_call(manager, "Inhibit", "ssss", ("sleep:idle", "PIIDigger", "Scan in progress", "block"))
    with open_dbus_connection("SYSTEM", enable_fds=True) as connection:
        reply = connection.send_and_get_reply(message, timeout=_LOGIND_REPLY_TIMEOUT_SECONDS)
    lock_fd: int = unwrap_msg(reply)[0].to_raw_fd()
    # A descriptor received over a socket stays open across exec.  A worker
    # must not hold a copy, or the lock would outlive a killed coordinator.
    os.set_inheritable(lock_fd, False)
    return lock_fd


def _take_logind_lock_bounded() -> int:
    """Run _take_logind_lock() on a daemon thread, giving up after the time limit.

    If the caller stops waiting, a lock that arrives later is closed at once.
    """
    outcome: Future[int] = Future()

    def _attempt() -> None:
        try:
            outcome.set_result(_take_logind_lock())
        except Exception as exc:
            outcome.set_exception(exc)

    def _close_late_lock(late: Future[int]) -> None:
        if late.exception() is None:
            os.close(late.result())

    threading.Thread(target=_attempt, daemon=True).start()
    try:
        return outcome.result(timeout=_LOGIND_REPLY_TIMEOUT_SECONDS)
    except BaseException:
        outcome.add_done_callback(_close_late_lock)
        raise


@contextmanager
def _wakepy_mode() -> Iterator[Any]:
    """keep.running(), except that a failure while leaving it is ignored.

    wakepy raises if it cannot release its request, for example when the
    desktop session ended during the scan.  That must not turn a finished scan
    into a crash, and the session that held the request is gone anyway.  It is
    not logged: run_coordinator has usually stopped the log listener by then.
    """
    mode = keep.running(on_fail="pass")
    wake_mode = mode.__enter__()
    try:
        yield wake_mode
    finally:
        with suppress(Exception):
            mode.__exit__(None, None, None)


@contextmanager
def prevent_sleep(logger: logging.Logger) -> Iterator[SleepStatus]:
    """Keep the machine awake inside the block, and yield how that went.

    This never raises for a failed attempt: a scan runs the same without it.
    """
    with _wakepy_mode() as wake_mode:
        if wake_mode.active:
            yield SleepStatus.ACTIVE
            return
        # wakepy logs this itself, but below the level library logs are routed at.
        logger.info("sleep prevention: wakepy not active: %s", wake_mode.result.get_failure_text(style="inline"))
        if sys.platform != "linux":
            yield SleepStatus.UNAVAILABLE
            return

        # Broad on purpose: no bus, a refusal, a timeout and an unexpected
        # reply all mean the same thing here.
        lock_fd: int | None = None
        try:
            lock_fd = _take_logind_lock_bounded()
        except Exception as exc:
            logger.info("sleep prevention: logind lock not available: %r", exc)
        # Outside the except, so a scan failure is not chained to the bus error.
        if lock_fd is None:
            yield SleepStatus.UNAVAILABLE_LINUX
            return
        try:
            yield SleepStatus.ACTIVE_LOGIND
        finally:
            os.close(lock_fd)
