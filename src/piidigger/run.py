from __future__ import annotations

from datetime import datetime
import multiprocessing as mp
import re
import socket
from typing import Any

from piidigger.models.config import Config
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.coordinator import run_coordinator
from piidigger.orchestration.logging_setup import start_listener
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.worker import start_worker_pool
from piidigger.outputhandlers import CsvSink, JsonSink, TextSink


_ALL_FORMATS: frozenset[str] = frozenset({"csv", "json", "text"})
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


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
      2. Start logging listener
      3. Build WorkerContext; start worker pool
      4. Run coordinator (seeds tasks, fan-out loop, teardown)

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

    ctx = WorkerContext(
        config=config,
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=stop_event,
    )
    workers = start_worker_pool(ctx, config.max_workers)

    progress = ProgressDisplay()
    progress.start()

    run_coordinator(ctx, workers, listener, sinks, progress)

    return 0
