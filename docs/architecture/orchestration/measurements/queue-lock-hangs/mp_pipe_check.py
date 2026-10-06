"""Check the per-worker pipe design against the same kills that break mp.Queue.

Each worker gets its own duplex Pipe.  The parent closes its copy of the child
end after start(), so the child's death closes the last handle and the parent
sees EOF instead of waiting forever.
"""

from __future__ import annotations

import multiprocessing as mp
import multiprocessing.connection as mpc
import os
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


def _big_sender(conn):
    conn.send_bytes(b"x" * (4 * 1024 * 1024))  # blocks: parent is not reading yet
    time.sleep(60)


def _idle_receiver(conn):
    conn.recv()


def _start(target):
    parent, child = mp.Pipe(duplex=True)
    p = mp.Process(target=target, args=(child,))
    p.start()
    child.close()  # the child's death must close the last copy of its end
    return p, parent


def scenario_partial_write() -> str:
    p, conn = _start(_big_sender)
    time.sleep(1.5)  # child is mid send_bytes()
    p.kill()
    p.join()

    def read() -> str:
        ready = mpc.wait([conn], timeout=3)
        if not ready:
            return "nothing ready"
        try:
            data = conn.recv_bytes()
        except EOFError:
            return "EOFError"
        except OSError as exc:
            return f"OSError({exc.errno})"
        return f"got {len(data)} bytes"

    return _within(read, 10)


def scenario_idle_kill() -> str:
    """Killing a worker blocked in recv() must not affect its siblings."""
    a, conn_a = _start(_idle_receiver)
    b, conn_b = _start(_idle_receiver)
    time.sleep(1.5)
    a.kill()
    a.join()
    try:
        conn_a.send("task")
        sent_to_dead = "send succeeded"
    except OSError as exc:
        sent_to_dead = f"send raised {type(exc).__name__}"
    conn_b.send("task")
    b.join(5)
    return f"dead worker: {sent_to_dead}; sibling exited={b.exitcode == 0}"


if __name__ == "__main__":
    print(f"platform={sys.platform} python={sys.version.split()[0]}", flush=True)
    print(f"partial_write: {scenario_partial_write()}", flush=True)
    print(f"idle_kill: {scenario_idle_kill()}", flush=True)
    os._exit(0)
