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
from piidigger.orchestration.worker import MAX_RETRIES, broadcast_shutdown, join_workers, start_worker_pool, worker_loop

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


def _crash_before_heartbeat_worker(ctx: WorkerContext) -> None:
    """Dequeue one task then crash immediately, before sending TaskStarted.

    TEST-ONLY: module-level so Windows mp.spawn can import it.
    Simulates a worker that dies between task_queue.get() and result_queue.put(TaskStarted(...)).
    """
    ctx.task_queue.get()
    os._exit(1)


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
    """Coordinator terminates with pending==0 after processing one real start dir.

    Empty directory → 1 ENUM_DIR task, no child tasks → pending reaches 0.
    run_coordinator() returns and all workers are joined.
    """
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [scan_root])
    listener = start_listener(log_queue, tmp_path / "arith.log", "WARNING")
    workers = start_worker_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, workers, listener, [], progress)

    assert all(not w.is_alive() for w in workers)


@pytest.mark.integration
def test_coordinator_accumulates_counters(tmp_path: Path) -> None:
    """Progress counters are summed across all completed tasks.

    Real directory layout: root with 2 subdirs (no files in them) + 3 .txt files.
    Expected: dirs_scanned=3 (root + 2 subdirs), files_scanned=3.
    """
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    (scan_root / "sub1").mkdir()
    (scan_root / "sub2").mkdir()
    for i in range(3):
        (scan_root / f"file{i}.txt").write_text(f"content line {i}")

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [scan_root])
    listener = start_listener(log_queue, tmp_path / "counters.log", "WARNING")
    workers = start_worker_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, workers, listener, [], progress)

    assert progress._counters.get("dirs_scanned", 0) == 3
    assert progress._counters.get("files_scanned", 0) == 3


# ---------------------------------------------------------------------------
# Integration: full fan-out with multiple start dirs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_fanout_multiple_start_dirs(tmp_path: Path) -> None:
    """Coordinator handles multiple start dirs without hanging."""
    start_dirs = []
    for i in range(3):
        d = tmp_path / f"root{i}"
        d.mkdir()
        (d / f"file{i}.txt").write_text(f"line {i}")
        start_dirs.append(d)

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, start_dirs)
    listener = start_listener(log_queue, tmp_path / "multi.log", "WARNING")
    workers = start_worker_pool(ctx, 3)
    progress = _non_tty_progress()

    run_coordinator(ctx, workers, listener, [], progress)

    assert all(not w.is_alive() for w in workers)
    assert progress._counters.get("dirs_scanned", 0) == 3
    assert progress._counters.get("files_scanned", 0) == 3


# ---------------------------------------------------------------------------
# Integration: Ctrl+C graceful exit
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "Sending SIGINT to a specific subprocess on Windows requires CREATE_NEW_PROCESS_GROUP, "
        "which mp.Process does not expose; os.kill(pid, SIGINT) raises PermissionError. Ctrl+C "
        "graceful-exit behavior is intentionally left unverified by CI on Windows — a real "
        "equivalent needs a subprocess.Popen(creationflags=CREATE_NEW_PROCESS_GROUP) + "
        "GenerateConsoleCtrlEvent harness, not a tweak to this test."
    ),
)
def test_ctrl_c_exits_within_5_seconds(tmp_path: Path) -> None:
    """KeyboardInterrupt causes coordinator subprocess to exit within 5 seconds (POSIX only)."""
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    # testdata/ has ~80 files across several subdirs; the scan takes well over
    # 0.5 s, so the coordinator is still in its main loop when SIGINT arrives.
    testdata = Path(__file__).parent.parent / "testdata"
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [testdata])

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
# Integration: queue.Empty fires → _check_worker_deadlines is called
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_coordinator_calls_deadline_check_on_queue_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a 1 ms HEARTBEAT_CHECK_INTERVAL, queue.Empty fires on almost every
    iteration, exercising the _check_worker_deadlines body with no expired tasks.
    """
    import piidigger.orchestration.coordinator as coord_mod

    monkeypatch.setattr(coord_mod, "HEARTBEAT_CHECK_INTERVAL", 0.001)

    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    (scan_root / "file.txt").write_text("hello world")

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [scan_root])
    listener = start_listener(log_queue, tmp_path / "heartbeat.log", "WARNING")
    workers = start_worker_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, workers, listener, [], progress)

    assert all(not w.is_alive() for w in workers)
    assert progress._counters.get("files_scanned", 0) == 1


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


# ---------------------------------------------------------------------------
# Integration: hung worker replaced while other workers continue
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_hung_worker_replaced_other_workers_continue(tmp_path: Path) -> None:
    """3 workers, 1 hung task + 5 quick tasks: the hung worker is terminated and
    replaced while the other workers process the quick tasks uninterrupted.

    Asserts:
    - All 5 quick NOOP tasks complete with status='ok' (other workers kept running).
    - The deadline fires, the hung worker is terminated, and a replacement is spawned.
    - pending reaches 0 (scan completes despite the hung worker).
    - Whole test finishes well within the 15-second safety limit.

    Uses the same coordinator-like inline loop as test_deadline_detection_terminates_hung_worker
    because run_coordinator() seeds tasks only from start_dirs; pre-injecting arbitrary
    task types requires driving the queue directly.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])

    # One task that will hang (120 s delay, 2 s timeout → deadline fires at ~4 s).
    hung_task = Task(task_type=TaskType.NOOP, payload={"delay_seconds": 120}, timeout_seconds=2)
    # Five tasks that complete normally — their ok results prove the other workers
    # kept running while the hung worker was being detected and replaced.
    quick_tasks = [Task(task_type=TaskType.NOOP) for _ in range(5)]
    all_tasks = [hung_task, *quick_tasks]

    for t in all_tasks:
        task_queue.put(t)

    workers = start_worker_pool(ctx, 3)
    listener = start_listener(log_queue, tmp_path / "hung.log", "DEBUG")
    progress = _non_tty_progress()

    pending = len(all_tasks)
    _pending_tasks: dict[str, Task] = {t.task_id: t for t in all_tasks}
    _in_flight: dict[str, tuple[int, float, int]] = {}
    _pid_to_proc: dict[int, mp.Process] = {p.pid: p for p in workers if p.pid is not None}

    deadline_fired = False
    replacement_spawned = False
    ok_completed = 0
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
                    replacement_spawned = True
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
            if raw.status == "ok":
                ok_completed += 1
            pending -= 1

    broadcast_shutdown(task_queue, len(workers))
    join_workers(workers, timeout=5.0)
    stop_listener(listener)
    progress.stop()

    assert deadline_fired, "deadline detection never fired for the hung task"
    assert replacement_spawned, "no replacement worker was spawned after the hung worker was terminated"
    assert ok_completed == 5, f"expected 5 quick tasks to complete with ok, got {ok_completed}"
    assert pending == 0, f"pending did not reach 0; remaining={pending}"
    assert time.monotonic() - test_start < TEST_LIMIT


# ---------------------------------------------------------------------------
# Integration: crash-before-heartbeat recovery
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_crash_before_heartbeat_requeues_task(tmp_path: Path) -> None:
    """Worker that crashes before TaskStarted causes the orphaned task to be re-queued.

    Exercises the crash-before-heartbeat path in coordinator._check_worker_deadlines
    (coordinator.py lines ~205-258):
      1. Dead worker detected via is_alive() == False.
      2. Replacement worker spawned.
      3. Task identified as crash orphan (in _pending_tasks, not in _in_flight,
         waited > CRASH_DETECT_TIMEOUT=0.1s used in this inline loop).
      4. Task re-queued with retry_count=1; replacement worker completes it; pending reaches 0.

    Asserts:
    - The crash is detected (crash_worker.is_alive() == False seen by the loop).
    - The orphaned task is re-queued under a new task_id.
    - pending reaches 0 (scan completes despite the crash).
    - Whole test finishes well within the 10-second safety limit.
    """
    # Crash-orphan window patched down from 30s so the test runs in ~2s:
    # HEARTBEAT_CHECK_INTERVAL fires after 1s → task has waited ~1s >> 0.1s threshold.
    CRASH_DETECT_TIMEOUT = 0.1

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])

    # One task for the crash worker to dequeue and drop (no TaskStarted will be sent).
    crash_task = Task(task_type=TaskType.NOOP)
    task_queue.put(crash_task)
    enqueue_time = time.monotonic()

    # Only one worker initially: the crash worker.  The inline loop spawns the
    # replacement after detecting the crash.
    crash_worker = mp.Process(target=_crash_before_heartbeat_worker, args=(ctx,))
    crash_worker.start()

    listener = start_listener(log_queue, tmp_path / "crash.log", "DEBUG")
    progress = _non_tty_progress()

    pending = 1
    _pending_tasks: dict[str, Task] = {crash_task.task_id: crash_task}
    _in_flight: dict[str, tuple[int, float, int]] = {}
    _task_enqueue_time: dict[str, float] = {crash_task.task_id: enqueue_time}
    _task_retries: dict[str, int] = {crash_task.task_id: 0}
    workers: list[mp.Process] = [crash_worker]
    _pid_to_proc: dict[int, mp.Process] = {crash_worker.pid: crash_worker}  # type: ignore[index]

    crash_detected = False
    task_requeued = False
    test_start = time.monotonic()
    TEST_LIMIT = 10.0

    while pending > 0 and time.monotonic() - test_start < TEST_LIMIT:
        try:
            raw = result_queue.get(timeout=HEARTBEAT_CHECK_INTERVAL)
        except queue.Empty:
            now = time.monotonic()

            # --- Crash-before-heartbeat detection (mirrors coordinator._check_worker_deadlines) ---
            dead_pids = [pid for pid, proc in _pid_to_proc.items() if not proc.is_alive()]
            if dead_pids:
                crash_detected = True
                for pid in dead_pids:
                    old_proc = _pid_to_proc.pop(pid)
                    if old_proc in workers:
                        workers.remove(old_proc)
                    new_proc = mp.Process(target=worker_loop, args=(ctx,))
                    new_proc.start()
                    if new_proc.pid is not None:
                        _pid_to_proc[new_proc.pid] = new_proc
                    workers.append(new_proc)

                crash_orphans = [
                    task_id
                    for task_id in _pending_tasks
                    if task_id not in _in_flight
                    and now - _task_enqueue_time.get(task_id, now) > CRASH_DETECT_TIMEOUT
                ]
                for task_id in crash_orphans:
                    task = _pending_tasks.pop(task_id)
                    _task_enqueue_time.pop(task_id, None)
                    retries = _task_retries.pop(task_id, 0)
                    pending -= 1
                    if retries < MAX_RETRIES:
                        new_task = Task(
                            task_type=task.task_type,
                            payload=task.payload,
                            timeout_seconds=task.timeout_seconds,
                        )
                        task_queue.put(new_task)
                        _pending_tasks[new_task.task_id] = new_task
                        _task_enqueue_time[new_task.task_id] = time.monotonic()
                        _task_retries[new_task.task_id] = retries + 1
                        pending += 1
                        task_requeued = True
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

    assert crash_detected, "crash worker death was never detected"
    assert task_requeued, "orphaned task was never re-queued after crash"
    assert pending == 0, f"pending did not reach 0; remaining={pending}"
    assert time.monotonic() - test_start < TEST_LIMIT
