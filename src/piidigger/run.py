from __future__ import annotations

import contextlib
import logging
import math
import multiprocessing as mp
import os
import re
import socket
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from wakepy import keep

from piidigger.models.config import Config
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.coordinator import run_coordinator
from piidigger.orchestration.logging_setup import (
    build_worker_logger,
    route_library_logs,
    setup_warning_capture,
    start_listener,
    stop_library_log_routing,
    stop_listener,
)
from piidigger.orchestration.pool import WorkerPool, spawn_worker
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.secure_delete import secure_rmtree
from piidigger.orchestration.worker import resolve_exclude_dirs
from piidigger.outputhandlers import HANDLER_REGISTRY, CsvSink, JsonSink, TextSink

_ALL_FORMATS: frozenset[str] = frozenset(HANDLER_REGISTRY)
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_ADMIN_PROMPT_TIMEOUT: int = 10

# Process exit codes.  A scan that did not finish must never report success —
# automation gating on the exit code would treat a truncated scan as clean.
EXIT_OK: int = 0
EXIT_ABORTED: int = 1  # refused to start (e.g. admin check declined)
EXIT_INCOMPLETE: int = 2  # ran, but work was left outstanding, workers could not start, or results were not all written
EXIT_INTERRUPTED: int = 130  # CTRL-C; 128 + SIGINT, the shell convention


def _is_admin() -> bool:
    """Return True if the current process has administrator/root privileges."""
    import ctypes

    try:
        return os.geteuid() == 0
    except AttributeError:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0  # type: ignore[attr-defined]


def _prompt_admin_continue(timeout: int = _ADMIN_PROMPT_TIMEOUT) -> bool:
    """Prompt the user to continue when PIIDigger is not running as administrator.

    Reads from stdin with a timeout.  Defaults to continuing (Y) if no input
    arrives within *timeout* seconds — non-interactive callers are not blocked.
    Returns True to proceed, False to abort.
    """
    print(
        f"Admin user not detected.  A full disk scan may not be possible.  Continue (Y/n) [{timeout}s]: ",
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
    admin = _is_admin()
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
    wake_mode: Any,
) -> None:
    """Emit a startup configuration summary to the event log and run logger."""
    plural = "es" if worker_count != 1 else ""
    sleep_active = getattr(wake_mode, "active", False)
    sleep_status = "Active" if sleep_active else "Unavailable on this platform"
    sleep_markup = "[green]Active[/green]" if sleep_active else "[yellow]Unavailable on this platform[/yellow]"
    dirs = ", ".join(str(d) for d in config.start_dirs)

    log_entries = [
        f"Performance: {config.performance} — {worker_count} worker process{plural}",
        f"Scan directories: {dirs}",
        f"Sleep prevention: {sleep_status}",
        "Press CTRL-C to terminate the scan",
    ]
    display_entries = [
        f"[bold white]Performance:[/bold white] [cyan]{config.performance} — {worker_count} worker process{plural}[/cyan]",
        f"[bold white]Scan directories:[/bold white] [cyan]{dirs}[/cyan]",
        f"[bold white]Sleep prevention:[/bold white] {sleep_markup}",
        "Press CTRL-C to terminate the scan",
    ]

    progress.set_startup_info(display_entries)
    for msg in log_entries:
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


def _open_sinks(config: Config, logger: logging.Logger) -> list[Any] | None:
    """Create the results folder and open every sink, or return None if any fails.

    Sinks raise OSError rather than log, so the failure is reported here: to the
    log file and, since the progress display has not started yet, to stderr.
    Sinks already opened are closed again.
    """
    opened: list[Any] = []
    try:
        config.results.path.mkdir(parents=True, exist_ok=True)
        for sink in _build_sinks(config):
            sink.open()
            opened.append(sink)
    except OSError as exc:
        logger.error("cannot open results file, aborting: %s", exc)
        print(f"Error: cannot open results file: {exc}", file=sys.stderr)  # noqa: T201 — user-facing
        for sink in opened:
            with contextlib.suppress(OSError):
                sink.close()
        return None
    return opened


def run_scan(config: Config) -> int:
    """Run a full PII scan against config.  Returns a process exit code.

    Wiring order:
      1. Start logging listener; create run-level logger; route library logs
      2. Admin privilege check (prompts user if not elevated and admin_check=True)
      3. Build and open output sinks (create parent dirs as needed).  Any
         failure aborts the run: there would be nowhere to put the results.
      4. Build WorkerContext; start worker pool
      5. Start progress display; emit startup config summary
      6. Run coordinator (seeds tasks, fan-out loop, teardown)

    Teardown (join workers, flush sinks, stop listener, stop progress)
    is owned by run_coordinator's finally block.  The temp workspace is owned
    here and removed in a finally, so an exception escaping the coordinator
    cannot leave extracted archive members — plaintext PII — on disk.

    Exit codes: EXIT_OK, EXIT_ABORTED, EXIT_INCOMPLETE, EXIT_INTERRUPTED.
    """
    log_queue: mp.Queue[object] = mp.Queue()
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    listener = start_listener(log_queue, config.log_file, config.log_level)
    setup_warning_capture(log_queue)
    route_library_logs(log_queue)
    run_logger = build_worker_logger(log_queue, "run")

    # Admin check and sink opening both happen before progress.start() takes
    # over the terminal, so their prompts and errors print cleanly.
    sinks = _open_sinks(config, run_logger) if _check_admin(config, run_logger) else None
    if sinks is None:
        stop_listener(listener)
        stop_library_log_routing()
        return EXIT_ABORTED

    # Create a PIIDigger-owned temp root and exclude it from directory scanning
    # so ENUM_DIR workers never attempt to scan extracted archive members.
    # mkdtemp() can return a path through a symlink alias (macOS /var ->
    # /private/var) or a Windows 8.3 short name; resolving it keeps the logged
    # path and the per-task extraction dirs consistent with the exclude pattern.
    temp_base: Path = Path(tempfile.mkdtemp(prefix="piidigger_")).resolve()
    run_logger.info("temp workspace: %s", temp_base)

    # Never scan our own output: the results and log folders hold every PAN and
    # email already found.  Resolved here because the defaults are relative and
    # resolve_exclude_dirs() leaves relative patterns unchanged.
    # Resolve every exclude pattern once so it matches the resolved paths
    # _is_excluded() compares against; see resolve_exclude_dirs().
    raw_exclude_dirs = [
        *config.exclude_dirs,
        str(config.results.path.resolve()),
        str(config.log_file.parent.resolve()),
        str(temp_base),
    ]
    exclude_dirs = resolve_exclude_dirs(raw_exclude_dirs)
    for raw, resolved in zip(raw_exclude_dirs, exclude_dirs, strict=True):
        if raw != resolved:
            run_logger.debug("exclude_dirs: %s resolves to %s", raw, resolved)
    runtime_config = config.model_copy(update={"exclude_dirs": exclude_dirs})

    logical_cores = os.cpu_count() or 1
    physical_cores = psutil.cpu_count(logical=False) or logical_cores
    worker_count = _resolve_workers(config.performance, physical_cores, logical_cores)
    ctx = WorkerContext(
        config=runtime_config,
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=stop_event,
        temp_base=temp_base,
        n_workers=worker_count,
    )
    pool = WorkerPool(lambda: spawn_worker(ctx), logger=build_worker_logger(log_queue, "pool"))
    pool.start(worker_count)

    progress = ProgressDisplay()
    progress.start()

    try:
        with keep.running(on_fail="pass") as wake_mode:
            _emit_startup_info(progress, run_logger, config, worker_count, wake_mode)
            outcome = run_coordinator(ctx, pool, listener, sinks, progress)
    finally:
        # secure_rmtree, not shutil.rmtree: a worker killed by terminate() never
        # unwinds its own finally, so its extracted members survive to here.
        secure_rmtree(temp_base)
        # run_coordinator stops the listener during its own teardown.  This is
        # the backstop for anything that raises before that teardown runs.
        # Without it the listener thread is left running and queued records,
        # including the one describing the failure, may never reach the log
        # file.  Stopping an already-stopped listener is a no-op.
        stop_listener(listener)
        stop_library_log_routing()

    # No logging from here on: the listener is stopped, so records would be
    # dropped.  run_coordinator has already logged each of these outcomes.
    if outcome.interrupted:
        return EXIT_INTERRUPTED
    if outcome.unfinished or outcome.workers_failed or outcome.sinks_failed:
        return EXIT_INCOMPLETE
    return EXIT_OK
