"""M1: crash a worker right after it sends a result, then see if the scan hangs.

One trial per process.  Drives the real run_coordinator / WorkerPool /
worker_loop; only the NOOP handler is swapped for a fault-injecting one:

- "big" task: returns an ENUM_DIR-shaped result with `children` leaf tasks
  (the large message).  With --arm big, the first worker to finish it is armed.
- "leaf" task: sleeps `sleep` seconds and returns.  With --arm small, the first
  worker to finish a background leaf is armed instead (control case).
- An armed worker calls os.abort() at the start of its very next task, while
  its feeder thread may still be pushing the result it just put.

Exactly one worker crashes per trial (an O_EXCL marker file arbitrates).
Prints one JSON line describing the outcome.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import multiprocessing as mp
import os
import pickle
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from piidigger.models.config import Config
from piidigger.models.tasks import Task, TaskResult, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.coordinator import CoordinatorResult, run_coordinator
from piidigger.orchestration.logging_setup import start_listener
from piidigger.orchestration.pool import WorkerPool
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.worker import _loop

_ARMED = False
_ARMED_AT = 0.0
_CRASH_DELAY = 0.0
_CRASH_BUSY = False
_WORK = ""


def _try_arm(marker: str) -> None:
    global _ARMED
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return
    os.write(fd, f"{os.getpid()} {time.monotonic()}".encode())
    os.close(fd)
    global _ARMED_AT
    _ARMED_AT = time.monotonic()
    _ARMED = True


def _big_result(task: Task, n: int, pad: int) -> TaskResult:
    new_tasks = [
        {
            "task_type": TaskType.NOOP,
            "payload": {
                "kind": "leaf",
                "display_path": f"/srv/share/dept/{i:08d}/" + "x" * pad,
                "file_path": f"/srv/share/dept/{i:08d}/" + "x" * pad,
                "ext": ".txt",
                "mime": None,
                "size": 4096,
                "depth": 0,
            },
            "timeout_seconds": 30,
        }
        for i in range(n)
    ]
    return TaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="ok",
        new_tasks=new_tasks,
        counters={"dirs_scanned": 1, "files_found": n},
        worker_pid=os.getpid(),
    )


def _crash(ctx: WorkerContext) -> None:
    """Simulate parsing a poison file for _CRASH_DELAY seconds, then die."""
    end = time.monotonic() + _CRASH_DELAY
    if _CRASH_BUSY:
        while time.monotonic() < end:
            pass
    else:
        time.sleep(_CRASH_DELAY)
    buffered = len(getattr(ctx.result_queue, "_buffer", ()))
    with open(os.path.join(_WORK, "abort"), "w") as fh:
        fh.write(json.dumps({"since_result_ms": round((time.monotonic() - _ARMED_AT) * 1000, 2), "buffered": buffered}))
    os.abort()


def _fault_noop(task: Task, ctx: WorkerContext, _logger: logging.Logger) -> TaskResult:
    if _ARMED:
        _crash(ctx)
    p = task.payload
    if p.get("kind") == "big":
        with open(os.path.join(_WORK, "big_runs"), "a") as fh:
            fh.write(f"{os.getpid()} ")
        result = _big_result(task, p["children"], p["pad"])
        if p.get("arm") == "big":
            _try_arm(p["marker"])
        return result
    if p.get("sleep"):
        time.sleep(p["sleep"])
    if p.get("arm") == "small":
        _try_arm(p["marker"])
    return TaskResult(task_id=task.task_id, task_type=task.task_type, status="ok", worker_pid=os.getpid())


def _fault_worker(ctx: WorkerContext, work: str, delay: float, busy: bool) -> None:
    """Module-level so the forkserver/spawn child can import it."""
    global _WORK, _CRASH_DELAY, _CRASH_BUSY
    _WORK, _CRASH_DELAY, _CRASH_BUSY = work, delay, busy
    _loop.DISPATCH[TaskType.NOOP] = _fault_noop
    _loop.worker_loop(ctx)


def _spawn(ctx: WorkerContext, work: str, delay: float, busy: bool) -> mp.Process:
    proc = mp.Process(target=_fault_worker, args=(ctx, work, delay, busy), daemon=True)
    proc.start()
    return proc


def _classify_hang(thread: threading.Thread, ctx: WorkerContext) -> dict[str, Any]:
    frame = sys._current_frames().get(thread.ident or -1)
    stack = [f.name for f in traceback.extract_stack(frame)] if frame else []
    wlock = getattr(ctx.result_queue, "_wlock", None)
    held = None
    if wlock is not None:
        got = wlock.acquire(False)
        if got:
            wlock.release()
        held = not got
    if any(n in ("recv_bytes", "_recv_bytes", "_recv") for n in stack):
        kind = "coordinator-stuck-in-recv"
    elif held:
        kind = "result-wlock-held"
    else:
        kind = "other"
    return {"hang_kind": kind, "wlock_held": held, "stack_tail": stack[-6:]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--children", type=int, default=10000)
    ap.add_argument("--pad", type=int, default=60)
    ap.add_argument("--bg", type=int, default=2000, help="background leaf tasks seeded alongside")
    ap.add_argument("--bg-sleep", type=float, default=0.002)
    ap.add_argument("--arm", choices=["big", "small"], default="big")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--crash-delay", type=float, default=0.0, help="seconds into the next task before abort()")
    ap.add_argument("--crash-busy", action="store_true", help="spin (hold the GIL) instead of sleeping")
    ap.add_argument("--size-only", action="store_true")
    args = ap.parse_args()

    if args.size_only:
        probe = _big_result(Task(task_type=TaskType.NOOP), args.children, args.pad)
        print(json.dumps({"children": args.children, "result_bytes": len(pickle.dumps(probe))}))
        return

    work = Path(tempfile.mkdtemp(prefix="m1_"))
    marker = str(work / "armed")
    task_queue: mp.Queue[Any] = mp.Queue()
    result_queue: mp.Queue[Any] = mp.Queue()
    log_queue: mp.Queue[Any] = mp.Queue()
    ctx = WorkerContext(
        config=Config(start_dirs=[]),
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=mp.Event(),
    )
    listener = start_listener(log_queue, work / "m1.log", "INFO")
    pool = WorkerPool(
        lambda: _spawn(ctx, str(work), args.crash_delay, args.crash_busy), logger=logging.getLogger("m1.pool")
    )
    pool.start(args.workers)
    progress = ProgressDisplay()
    progress._is_tty = False

    big = Task(
        task_type=TaskType.NOOP,
        payload={"kind": "big", "children": args.children, "pad": args.pad, "arm": args.arm, "marker": marker},
    )
    bg = [
        Task(
            task_type=TaskType.NOOP, payload={"kind": "leaf", "sleep": args.bg_sleep, "arm": args.arm, "marker": marker}
        )
        for _ in range(args.bg)
    ]
    # Big first so it is taken immediately while the others chew on background work.
    seeds = [big, *bg]

    outcome: list[CoordinatorResult] = []
    t0 = time.monotonic()
    runner = threading.Thread(
        target=lambda: outcome.append(run_coordinator(ctx, pool, listener, [], progress, seed_tasks=seeds)),
        daemon=True,
    )
    runner.start()
    runner.join(args.timeout)
    elapsed = time.monotonic() - t0

    record: dict[str, Any] = {
        "workers": args.workers,
        "children": args.children,
        "bg": args.bg,
        "arm": args.arm,
        "crash_delay_ms": round(args.crash_delay * 1000),
        "busy": args.crash_busy,
        "crashed": os.path.exists(marker),
        "elapsed": round(elapsed, 2),
    }
    big_runs = work / "big_runs"
    record["big_runs"] = len(big_runs.read_text().split()) if big_runs.exists() else 0
    abort = work / "abort"
    if abort.exists():
        record["abort"] = json.loads(abort.read_text())
    if runner.is_alive():
        record["outcome"] = "hang"
        record.update(_classify_hang(runner, ctx))
    else:
        record["outcome"] = "ok"
        record["unfinished"] = outcome[0].unfinished
    print(json.dumps(record), flush=True)

    for proc in pool.processes:
        with contextlib.suppress(Exception):
            proc.kill()
    os._exit(0)


if __name__ == "__main__":
    main()
