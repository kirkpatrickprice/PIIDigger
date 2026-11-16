"""M3: real scan, SIGKILL random workers at random moments, count hangs.

One trial per process.  run_scan runs on the main thread (it installs signal
handlers); a killer thread and a watchdog thread run beside it.

Usage: m3_random_kill_scan.py DATA OUT PRESET KILLS FIRST_KILL SPACING TIMEOUT
Prints one JSON line.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import piidigger.run as run_mod
from piidigger.models.config import Config, ResultsConfig

_real_run_coordinator = run_mod.run_coordinator
state: dict[str, Any] = {"kills": [], "pids_seen": set()}
_ctx: list[Any] = []


def _killer(pool: Any, kills: int, first: float, spacing: float) -> None:
    rng = random.Random()  # noqa: S311 - kill timing, not security
    time.sleep(first * rng.uniform(0.5, 1.5))
    for _ in range(kills):
        pids = sorted(pool.pids())
        if not pids:
            return
        pid = rng.choice(pids)
        try:
            os.kill(pid, signal.SIGKILL)
            state["kills"].append(round(time.monotonic() - state["t0"], 2))
        except ProcessLookupError:
            pass
        time.sleep(spacing * rng.uniform(0.5, 1.5))


def _wrapped(ctx: Any, pool: Any, *args: Any, **kwargs: Any) -> Any:
    _ctx.append(ctx)
    state["pool"] = pool
    threading.Thread(target=_killer, args=(pool, *state["kill_args"]), daemon=True).start()
    return _real_run_coordinator(ctx, pool, *args, **kwargs)


def _watchdog(timeout: float, main_ident: int) -> None:
    time.sleep(timeout)
    frame = sys._current_frames().get(main_ident)
    stack = [f.name for f in traceback.extract_stack(frame)] if frame else []
    held = None
    if _ctx:
        lock = _ctx[0].result_queue._wlock
        got = lock.acquire(False)
        if got:
            lock.release()
        held = not got
    if any(n in ("recv_bytes", "_recv_bytes", "_recv") for n in stack):
        kind = "coordinator-stuck-in-recv"
    elif held:
        kind = "result-wlock-held"
    else:
        kind = "other"
    print(
        json.dumps({**_summary(), "outcome": "hang", "hang_kind": kind, "wlock_held": held, "stack_tail": stack[-6:]}),
        flush=True,
    )
    pool = state.get("pool")
    for proc in getattr(pool, "processes", []) if pool else []:
        with contextlib.suppress(Exception):
            proc.kill()
    os._exit(0)


def _summary() -> dict[str, Any]:
    return {
        "preset": state["preset"],
        "kills_at": state["kills"],
        "elapsed": round(time.monotonic() - state["t0"], 1),
    }


def main() -> None:
    data, out, preset = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    kills, first, spacing, timeout = int(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6]), float(sys.argv[7])
    state.update(preset=preset, kill_args=(kills, first, spacing), t0=time.monotonic())
    run_mod.run_coordinator = _wrapped
    threading.Thread(target=_watchdog, args=(timeout, threading.get_ident()), daemon=True).start()
    config = Config(
        start_dirs=[data],
        performance=preset,
        admin_check=False,
        log_file=out / "logs" / "m3.log",
        results=ResultsConfig(path=out / "results", formats=["json"]),
    )
    rc = run_mod.run_scan(config)
    print(json.dumps({**_summary(), "outcome": "ok", "exit_code": rc}), flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
