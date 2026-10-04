"""Reproduce cross-process lock hazards in multiprocessing primitives.

Each scenario kills a process at a point where it holds a lock or is midway
through a pipe write, then checks whether the survivors can still make progress.
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import threading
import time


def _within(fn, timeout: float) -> str:
    out: list[str] = []

    def run() -> None:
        try:
            out.append(f"returned {fn()!r}")
        except BaseException as exc:  # noqa: BLE001
            out.append(f"raised {type(exc).__name__}")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return "HUNG" if t.is_alive() else out[0]


# 1. task_queue._rlock: an idle worker is killed while waiting in get().
def _idle_getter(q, tag, out):
    item = q.get()
    out.put((tag, item))


def scenario_rlock() -> str:
    q, out = mp.Queue(), mp.Queue()
    a = mp.Process(target=_idle_getter, args=(q, "A", out))
    a.start()
    time.sleep(1.5)  # A now holds q._rlock inside recv_bytes()
    b = mp.Process(target=_idle_getter, args=(q, "B", out))
    b.start()
    time.sleep(1.5)  # B now blocks acquiring q._rlock
    a.kill()
    a.join()
    q.put("task")
    result = _within(lambda: out.get(timeout=5), 7)
    b.kill()
    return result


# 2. result_queue: a writer is killed mid send_bytes() of a large message.
def _big_writer(q):
    q.put(b"x" * (4 * 1024 * 1024))  # far larger than the pipe buffer
    time.sleep(60)


def _small_writer(q):
    q.put("small")
    time.sleep(60)


def scenario_partial_write() -> str:
    q = mp.Queue()
    a = mp.Process(target=_big_writer, args=(q,))
    a.start()
    time.sleep(1.5)  # A's feeder is blocked mid-write (holding _wlock on POSIX)
    a.kill()
    a.join()
    b = mp.Process(target=_small_writer, args=(q,))
    b.start()
    time.sleep(1.0)

    def drain() -> str:
        got = []
        for _ in range(2):
            try:
                m = q.get(timeout=3)
            except Exception as exc:  # noqa: BLE001
                got.append(type(exc).__name__)
                continue
            got.append("small" if m == "small" else f"{type(m).__name__}[{len(m) if hasattr(m, '__len__') else '?'}]")
        return ",".join(got)

    result = _within(drain, 10)
    b.kill()
    return result


# 3. stop_event: a worker is killed while inside Event.is_set().
def _spin_is_set(ev):
    while True:
        ev.is_set()


def scenario_event() -> str:
    hung = 0
    for _ in range(20):
        ev = mp.Event()
        p = mp.Process(target=_spin_is_set, args=(ev,))
        p.start()
        time.sleep(0.3)
        p.kill()
        p.join()
        if _within(ev.set, 2) == "HUNG":
            hung += 1
    return f"set() hung in {hung}/20 trials"


if __name__ == "__main__":
    print(f"platform={sys.platform} python={sys.version.split()[0]} start={mp.get_start_method()}", flush=True)
    for name, fn in [("rlock", scenario_rlock), ("partial_write", scenario_partial_write), ("event", scenario_event)]:
        print(f"{name}: {fn()}", flush=True)
    import os

    os._exit(0)  # skip queue finalizers that may block on a wedged pipe
