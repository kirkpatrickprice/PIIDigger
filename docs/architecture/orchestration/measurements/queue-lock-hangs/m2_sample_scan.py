"""M2: run a real scan and measure how often a worker holds a queue write lock.

Wraps run_coordinator so that, once the pool is up, the coordinator process:
- samples result_queue._wlock and log_queue._wlock every ~1 ms with a
  non-blocking acquire (held = some worker's feeder is inside send_bytes());
- records the byte size of every message read from result_queue.

Prints one JSON summary.  POSIX only (Windows has no _wlock).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import piidigger.run as run_mod
from piidigger.models.config import Config, ResultsConfig

_real_run_coordinator = run_mod.run_coordinator
stats: dict[str, Any] = {}


def _bucket(n: int) -> str:
    for limit, label in ((4096, "<4KiB"), (65536, "4-64KiB"), (1 << 20, "64KiB-1MiB"), (10 << 20, "1-10MiB")):
        if n < limit:
            return label
    return ">=10MiB"


def _sampler(ctx: Any, stop: threading.Event) -> None:
    locks = {"result": ctx.result_queue._wlock, "log": ctx.log_queue._wlock}
    held = Counter()
    streak = {k: 0 for k in locks}
    streaks: dict[str, Counter[int]] = {k: Counter() for k in locks}
    samples = 0
    while not stop.is_set():
        samples += 1
        for name, lock in locks.items():
            if lock.acquire(False):
                lock.release()
                if streak[name]:
                    streaks[name][streak[name]] += 1
                streak[name] = 0
            else:
                held[name] += 1
                streak[name] += 1
        time.sleep(0.001)
    stats["samples"] = samples
    stats["held_fraction"] = {k: round(held[k] / max(samples, 1), 5) for k in locks}
    stats["hold_streaks_in_samples"] = {
        k: {"count": sum(v.values()), "max": max(v, default=0), ">=5": sum(c for s, c in v.items() if s >= 5)}
        for k, v in streaks.items()
    }


def _instrumented(ctx: Any, pool: Any, *args: Any, **kwargs: Any) -> Any:
    sizes: Counter[str] = Counter()
    largest = [0]
    q = ctx.result_queue
    real_recv = q._recv_bytes

    def recv(*a: Any, **k: Any) -> Any:
        data = real_recv(*a, **k)
        n = len(data)
        sizes[_bucket(n)] += 1
        largest[0] = max(largest[0], n)
        return data

    q._recv_bytes = recv
    stop = threading.Event()
    t = threading.Thread(target=_sampler, args=(ctx, stop), daemon=True)
    t0 = time.monotonic()
    t.start()
    try:
        return _real_run_coordinator(ctx, pool, *args, **kwargs)
    finally:
        stop.set()
        t.join()
        stats["workers"] = ctx.n_workers
        stats["scan_seconds"] = round(time.monotonic() - t0, 1)
        stats["result_messages"] = dict(sizes)
        stats["largest_message_bytes"] = largest[0]


def main() -> None:
    data, out, performance = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    plain = len(sys.argv) > 4 and sys.argv[4] == "plain"
    if not plain:
        run_mod.run_coordinator = _instrumented
    t0 = time.monotonic()
    config = Config(
        start_dirs=[data],
        performance=performance,
        admin_check=False,
        log_file=out / "logs" / "m2.log",
        results=ResultsConfig(path=out / "results", formats=["json"]),
    )
    rc = run_mod.run_scan(config)
    stats["exit_code"] = rc
    stats["plain"] = plain
    stats["run_scan_seconds"] = round(time.monotonic() - t0, 1)
    stats["performance"] = performance
    print(json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()
