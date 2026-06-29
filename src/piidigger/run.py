from __future__ import annotations

import logging
import math
import multiprocessing as mp
import os
import re
import socket
import sys
import threading
from datetime import datetime
from typing import Any

import psutil

from piidigger.models.config import Config
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.coordinator import run_coordinator
from piidigger.orchestration.logging_setup import build_worker_logger, setup_warning_capture, start_listener
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.worker import start_worker_pool
from piidigger.outputhandlers import CsvSink, JsonSink, TextSink

_ALL_FORMATS: frozenset[str] = frozenset({"csv", "json", "text"})
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_ADMIN_PROMPT_TIMEOUT: int = 10


def _prompt_admin_continue(timeout: int = _ADMIN_PROMPT_TIMEOUT) -> bool:
    """Prompt the user to continue when PIIDigger is not running as administrator.

    Reads from stdin with a timeout.  Defaults to continuing (Y) if no input
    arrives within *timeout* seconds — non-interactive callers are not blocked.
    Returns True to proceed, False to abort.
    """
    print(
        f"Admin user not detected.  A full disk scan may not be possible."
        f"  Continue (Y/n) [{timeout}s]: ",
        end="",
        flush=True,
    )
    result: list[str] = [""]
    ev = threading.Event()

    def _read() -> None:
        try:
            result[0] = sys.stdin.readline().strip()
        except (EOFError, OSError):
            pass
        ev.set()

    threading.Thread(target=_read, daemon=True).start()
    if not ev.wait(timeout=float(timeout)):
        print(f"\n(No response in {timeout}s — continuing scan)", flush=True)
        return True

    return result[0].lower() not in ("n", "no")


def _check_admin(config: Config, logger: logging.Logger) -> bool:
    """Check for administrator/root privileges and handle the result.

    Always performs the check and logs the outcome.  When *config.admin_check*
    is True and the process is not elevated, the user is prompted to confirm
    before scanning proceeds.  Returns True to proceed, False to abort.
    """
    from piidigger.globalfuncs import is_admin  # local: avoids circular import

    admin = is_admin()
    logger.info("admin check: running as %s", "administrator" if admin else "standard user")
    if admin:
        return True

    logger.warning("admin check: not running as administrator — a full disk scan may be incomplete")
    if not config.admin_check:
        return True  # check + log done; confirmation bypassed per config

    if not sys.stdin.isatty():
        logger.info("admin check: non-interactive mode — continuing without admin confirmation")
        return True

    return _prompt_admin_continue()


def _emit_startup_info(
    progress: ProgressDisplay,
    logger: logging.Logger,
    config: Config,
    worker_count: int,
) -> None:
    """Emit a startup configuration summary to the event log and run logger."""
    plural = "es" if worker_count != 1 else ""
    entries = [
        f"Performance: {config.performance} — {worker_count} worker process{plural}",
        "Scan directories: " + ", ".join(str(d) for d in config.start_dirs),
        "Sleep prevention: Not configured",
        "Press CTRL-C to terminate the scan",
    ]
    for msg in entries:
        progress.log_event("INFO", msg)
        logger.info(msg)


def _resolve_workers(performance: str, physical_cores: int, logical_cores: int) -> int:
    """Map a performance preset to a worker count."""
    if performance == "slow":
        return 1
    if performance == "fast":
        return max(1, logical_cores)
    if performance == "balanced":
        base_cores = physical_cores or logical_cores
        return max(1, math.ceil(base_cores * 0.75))
    raise ValueError(f"unknown performance preset: {performance}")


def _build_sinks(config: Config) -> list[Any]:
    """Instantiate output sinks based on config.results.

    Filenames are stamped with the scan start time so successive runs never
    overwrite each other: piidigger-<YYYYMMDD-HHMMSS>.<ext>
    """
    r = config.results
    active = _ALL_FORMATS if "all" in r.formats else _ALL_FORMATS & set(r.formats)
    if not active:
        return []

    hostname = _UNSAFE_CHARS.sub("_", socket.gethostname())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{hostname}-{timestamp}"

    sinks: list[Any] = []
    if "csv" in active:
        sinks.append(CsvSink(r.path / f"{stem}.csv"))
    if "json" in active:
        sinks.append(JsonSink(r.path / f"{stem}.json"))
    if "text" in active:
        sinks.append(TextSink(r.path / f"{stem}.txt"))
    return sinks


def run_scan(config: Config) -> int:
    """Run a full PII scan against config. Returns 0 on success.

    Wiring order:
      1. Build and open output sinks (create parent dirs as needed)
      2. Start logging listener; create run-level logger
      3. Admin privilege check (prompts user if not elevated and admin_check=True)
      4. Build WorkerContext; start worker pool
      5. Start progress display; emit startup config summary
      6. Run coordinator (seeds tasks, fan-out loop, teardown)

    Teardown (join workers, flush sinks, stop listener, stop progress)
    is owned by run_coordinator's finally block.
    """
    log_queue: mp.Queue[object] = mp.Queue()
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    config.results.path.mkdir(parents=True, exist_ok=True)
    sinks = _build_sinks(config)
    for sink in sinks:
        sink.open()

    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    listener = start_listener(log_queue, config.log_file, config.log_level)
    setup_warning_capture(log_queue)
    run_logger = build_worker_logger(log_queue, "run")

    # Admin check — must happen before progress.start() takes over the terminal
    if not _check_admin(config, run_logger):
        for sink in sinks:
            sink.close()
        listener.stop()
        return 1

    ctx = WorkerContext(
        config=config,
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=stop_event,
    )
    logical_cores = os.cpu_count() or 1
    physical_cores = psutil.cpu_count(logical=False) or logical_cores
    worker_count = _resolve_workers(config.performance, physical_cores, logical_cores)
    workers = start_worker_pool(ctx, worker_count)

    progress = ProgressDisplay()
    progress.start()
    _emit_startup_info(progress, run_logger, config, worker_count)

    run_coordinator(ctx, workers, listener, sinks, progress)

    return 0
