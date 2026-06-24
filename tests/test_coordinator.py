"""Tests for the Phase 2 coordinator — unit and integration.

All multiprocessing process targets are module-level functions so that
Windows spawn can import them without re-running test code.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import signal
import time
from pathlib import Path

import pytest

from piidigger.models.config import Config
from piidigger.models.tasks import Task, TaskResult, TaskStarted, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.coordinator import (
    HEARTBEAT_CHECK_INTERVAL,
    _run_with_internal_workers,
    run_coordinator,
)
from piidigger.orchestration.logging_setup import start_listener, stop_listener
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.worker import broadcast_shutdown, join_workers, start_worker_pool, worker_loop

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    task_queue: mp.Queue[object],
    result_queue: mp.Queue[object],
    log_queue: mp.Queue[object],
    stop_event: mp.Event,  # type: ignore[type-arg]
    start_dirs: list[Path] | None = None,
) -> WorkerContext:
    return WorkerContext(
        config=Config(start_dirs=start_dirs or []),
        task_queue=task_queue,
        result_queue=result_queue,
        log_queue=log_queue,
        stop_event=stop_event,
    )


def _non_tty_progress() -> ProgressDisplay:
    """ProgressDisplay forced into non-TTY mode so tests produce no rich output."""
    d = ProgressDisplay()
    d._is_tty = False
    return d




# ---------------------------------------------------------------------------
# Unit: Config.start_dirs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_accepts_start_dirs(tmp_path: Path) -> None:
    """Config stores the start_dirs list and is picklable (WorkerContext requires it)."""
    import pickle

    dirs = [tmp_path / "a", tmp_path / "b"]
    config = Config(start_dirs=dirs)
    assert config.start_dirs == dirs

    restored: Config = pickle.loads(pickle.dumps(config))
    assert restored.start_dirs == dirs


# ---------------------------------------------------------------------------
# Integration: pending arithmetic via full coordinator run
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_pending_arithmetic_single_start_dir(tmp_path: Path) -> None:
    """Coordinator terminates with pending==0 after fanning out one start dir.

    One ENUM_DIR at depth=0 → 2 ENUM_DIR (depth=1) + 3 SCAN_FILE = 5 new tasks.
    Each depth-1 ENUM_DIR is a leaf (returns immediately).
    Each SCAN_FILE returns counters only.
    Total tasks processed: 1 + 2 + 3 = 6.  run_coordinator() returns → pending was 0.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [Path("/synthetic/start")])
    listener = start_listener(log_queue, tmp_path / "arith.log", "WARNING")
    workers = start_worker_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, workers, listener, [], progress)

    assert all(not w.is_alive() for w in workers)


@pytest.mark.integration
def test_coordinator_accumulates_counters(tmp_path: Path) -> None:
    """Progress counters are summed across all completed tasks."""
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [Path("/synthetic/root")])
    listener = start_listener(log_queue, tmp_path / "counters.log", "WARNING")
    workers = start_worker_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, workers, listener, [], progress)

    # Stub layout (see _handle_enum_dir_stub in worker.py):
    #   depth-0 ENUM_DIR → dirs_scanned=1, dirs_found=2, files_found=3
    #   depth-1 ENUM_DIR × 2 → dirs_scanned=1 each
    #   SCAN_FILE × 3 → files_scanned=1, bytes_scanned=1024 each
    assert progress._counters.get("dirs_scanned", 0) == 3
    assert progress._counters.get("files_scanned", 0) == 3
    assert progress._counters.get("bytes_scanned", 0) == 3072


# ---------------------------------------------------------------------------
# Integration: full fan-out with multiple start dirs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_fanout_multiple_start_dirs(tmp_path: Path) -> None:
    """Coordinator handles multiple start dirs without hanging."""
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    start_dirs = [Path(f"/synthetic/root{i}") for i in range(3)]
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, start_dirs)
    listener = start_listener(log_queue, tmp_path / "multi.log", "WARNING")
    workers = start_worker_pool(ctx, 3)
    progress = _non_tty_progress()

    run_coordinator(ctx, workers, listener, [], progress)

    assert all(not w.is_alive() for w in workers)
    # 3 roots × (1+2+3) tasks each = 18 tasks total → 3 × 3 = 9 dirs_scanned, 3 × 3 = 9 files
    assert progress._counters.get("dirs_scanned", 0) == 9
    assert progress._counters.get("files_scanned", 0) == 9


# ---------------------------------------------------------------------------
# Integration: Ctrl+C graceful exit
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "Sending SIGINT to a specific subprocess on Windows requires CREATE_NEW_PROCESS_GROUP "
        "which mp.Process does not expose; os.kill(pid, SIGINT) raises PermissionError."
    ),
)
def test_ctrl_c_exits_within_5_seconds(tmp_path: Path) -> None:
    """KeyboardInterrupt causes coordinator subprocess to exit within 5 seconds (POSIX only)."""
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    # /slow_test/ prefix causes _handle_enum_dir_stub to return a SLOW_TEST task
    # (sleep 120s) instead of normal fan-out.  This guarantees the coordinator is
    # blocked on result_queue.get() when SIGINT is sent — no race on fast CI.
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [Path("/slow_test/root")])

    # _run_with_internal_workers is defined in piidigger.orchestration.coordinator
    # (an installed package), so Windows spawn can import it.  Workers are started
    # inside the subprocess — started mp.Process objects cannot be pickled.
    coord_proc = mp.Process(
        target=_run_with_internal_workers,
        args=(ctx, 2, str(tmp_path / "ctrlc.log")),
    )
    coord_proc.start()
    time.sleep(0.5)  # let the coordinator enter its main loop

    if coord_proc.pid is not None:
        os.kill(coord_proc.pid, signal.SIGINT)

    coord_proc.join(timeout=5.0)
    assert not coord_proc.is_alive(), "coordinator did not exit within 5 seconds after interrupt"


# ---------------------------------------------------------------------------
# Integration: deadline detection
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_deadline_detection_terminates_hung_worker(tmp_path: Path) -> None:
    """A slow NOOP task (delay_seconds=120, timeout=2s) triggers deadline detection.

    The coordinator-like inline loop below exercises the same deadline logic
    as run_coordinator._check_worker_deadlines() — verifying: the hung worker
    is terminated, a replacement is spawned, pending reaches 0, and the whole
    test completes well within the 15-second limit.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])

    slow_task = Task(task_type=TaskType.NOOP, payload={"delay_seconds": 120}, timeout_seconds=2)
    noop_task = Task(task_type=TaskType.NOOP)

    task_queue.put(slow_task)
    task_queue.put(noop_task)

    workers = start_worker_pool(ctx, 2)
    listener = start_listener(log_queue, tmp_path / "deadline.log", "DEBUG")
    progress = _non_tty_progress()

    # Inline coordinator-like loop: exercises the same pending + deadline logic
    # as run_coordinator without the seeding path (start_dirs=[]).
    pending = 2
    _in_flight: dict[str, tuple[int, float, int]] = {}
    _pending_tasks: dict[str, Task] = {
        slow_task.task_id: slow_task,
        noop_task.task_id: noop_task,
    }
    _pid_to_proc: dict[int, mp.Process] = {p.pid: p for p in workers if p.pid is not None}

    deadline_fired = False
    test_start = time.monotonic()
    TEST_LIMIT = 15.0

    while pending > 0 and time.monotonic() - test_start < TEST_LIMIT:
        try:
            raw = result_queue.get(timeout=HEARTBEAT_CHECK_INTERVAL)
        except queue.Empty:
            now = time.monotonic()
            for task_id, (pid, dispatch_time, timeout_secs) in list(_in_flight.items()):
                if now - dispatch_time > 2 * timeout_secs:
                    deadline_fired = True
                    _in_flight.pop(task_id)
                    _pending_tasks.pop(task_id, None)
                    old_proc = _pid_to_proc.pop(pid, None)
                    if old_proc is not None:
                        old_proc.terminate()
                        old_proc.join(timeout=2.0)
                        if old_proc in workers:
                            workers.remove(old_proc)
                    new_proc = mp.Process(target=worker_loop, args=(ctx,))
                    new_proc.start()
                    if new_proc.pid is not None:
                        _pid_to_proc[new_proc.pid] = new_proc
                    workers.append(new_proc)
                    pending -= 1
            continue

        if isinstance(raw, TaskStarted):
            task = _pending_tasks.get(raw.task_id)
            if task is not None:
                _in_flight[raw.task_id] = (raw.worker_pid, time.monotonic(), task.timeout_seconds)
            continue

        if isinstance(raw, TaskResult):
            _in_flight.pop(raw.task_id, None)
            _pending_tasks.pop(raw.task_id, None)
            pending -= 1

    broadcast_shutdown(task_queue, len(workers))
    join_workers(workers, timeout=5.0)
    stop_listener(listener)
    progress.stop()

    assert deadline_fired, "deadline detection never fired for the slow NOOP task"
    assert pending == 0, f"pending did not reach 0; remaining={pending}"
    assert time.monotonic() - test_start < TEST_LIMIT
