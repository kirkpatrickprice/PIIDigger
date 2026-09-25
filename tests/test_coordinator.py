"""Tests for the Phase 2 coordinator — unit and integration.

All multiprocessing process targets are module-level functions so that
Windows spawn can import them without re-running test code.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from piidigger.models.config import Config
from piidigger.models.tasks import ShutdownSentinel, Task, TaskResult, TaskStarted, TaskType
from piidigger.orchestration.context import WorkerContext
from piidigger.orchestration.coordinator import (
    CoordinatorResult,
    _run_with_internal_workers,
    build_seed_tasks,
    run_coordinator,
)
from piidigger.orchestration.logging_setup import start_listener
from piidigger.orchestration.pool import WorkerPool
from piidigger.orchestration.progress import ProgressDisplay
from piidigger.orchestration.worker import worker_loop

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


_POOL_LOG = logging.getLogger("tests.coordinator.pool")


def _spawn(ctx: WorkerContext, target: Callable[[WorkerContext], None] = worker_loop) -> mp.Process:
    """Start one daemonic process running target, as spawn_worker does for worker_loop."""
    proc = mp.Process(target=target, args=(ctx,), daemon=True)
    proc.start()
    return proc


def _start_pool(ctx: WorkerContext, n: int, *, targets: Sequence[Callable[[WorkerContext], None]] = ()) -> WorkerPool:
    """A started pool.

    The first len(targets) processes run those targets.  The rest, and every
    replacement the coordinator asks for, run the real worker_loop.
    """
    remaining = iter(targets)
    pool = WorkerPool(lambda: _spawn(ctx, next(remaining, worker_loop)), logger=_POOL_LOG)
    pool.start(n)
    return pool


def _crash_after_heartbeat_worker(ctx: WorkerContext) -> None:
    """Dequeue a task, announce it, then die without finishing it.

    TEST-ONLY: module-level so Windows mp.spawn can import it.  Simulates a
    worker killed mid-task, by a segfaulting parser for instance.  The result
    queue is flushed before exiting: put() hands off to a feeder thread, and
    os._exit() would otherwise kill that thread with the heartbeat still unsent.
    """
    item = ctx.task_queue.get()
    if isinstance(item, ShutdownSentinel):
        return
    ctx.result_queue.put(TaskStarted(task_id=item.task_id, worker_pid=os.getpid()))
    ctx.result_queue.close()
    ctx.result_queue.join_thread()
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
    pool = _start_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, pool, listener, [], progress)

    assert all(not w.is_alive() for w in pool.processes)


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
    pool = _start_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, pool, listener, [], progress)

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
    pool = _start_pool(ctx, 3)
    progress = _non_tty_progress()

    run_coordinator(ctx, pool, listener, [], progress)

    assert all(not w.is_alive() for w in pool.processes)
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
# Integration: sweep cadence
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_frequent_sweeps_do_not_disturb_a_normal_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a 1 ms sweep interval the health sweep runs on almost every
    iteration.  A healthy scan must come through that untouched.
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
    pool = _start_pool(ctx, 2)
    progress = _non_tty_progress()

    run_coordinator(ctx, pool, listener, [], progress)

    assert all(not w.is_alive() for w in pool.processes)
    assert progress._counters.get("files_scanned", 0) == 1


@pytest.mark.slow
def test_sweep_runs_while_results_keep_arriving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect 6 guard: detection must not wait for an idle result queue.

    One worker works through a stream of short tasks, so a message (heartbeat or
    result) arrives every fraction of a second and the queue is never quiet for
    a full sweep interval.  The old coordinator swept only on queue.Empty, so it
    would sweep zero times in this window; hung or crashed workers went
    undetected for as long as a scan stayed busy.
    """
    import piidigger.orchestration.coordinator as coord_mod

    sweeps: list[float] = []
    results: list[float] = []
    original_tick = coord_mod.HealthMonitor.tick
    original_handle = coord_mod._handle_result

    def counting_tick(self: coord_mod.HealthMonitor) -> coord_mod.SweepResult:
        sweeps.append(time.monotonic())
        return original_tick(self)

    def recording_handle(*args: object, **kwargs: object) -> None:
        results.append(time.monotonic())
        original_handle(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(coord_mod.HealthMonitor, "tick", counting_tick)
    monkeypatch.setattr(coord_mod, "_handle_result", recording_handle)

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])
    listener = start_listener(log_queue, tmp_path / "busy.log", "WARNING")
    pool = _start_pool(ctx, 1)
    progress = _non_tty_progress()

    seeds = [Task(task_type=TaskType.NOOP, payload={"delay_seconds": 0.25}) for _ in range(16)]
    run_coordinator(ctx, pool, listener, [], progress, seed_tasks=seeds)

    assert len(results) == 16
    busy = [t for t in sweeps if results[0] < t < results[-1]]
    assert len(busy) >= 2, f"only {len(busy)} sweep(s) ran during {results[-1] - results[0]:.1f}s of steady results"


# ---------------------------------------------------------------------------
# Integration: deadline detection, driven through the real run_coordinator
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_deadline_detection_replaces_hung_worker(tmp_path: Path) -> None:
    """A NOOP that sleeps far past its deadline is abandoned and its worker replaced.

    delay 120 s against a 2 s timeout, so the deadline (2 x timeout) fires at
    about 4 s.  The scan still completes, and the pool is the same size after.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])
    listener = start_listener(log_queue, tmp_path / "deadline.log", "DEBUG")
    pool = _start_pool(ctx, 2)
    original_pids = pool.pids()
    progress = _non_tty_progress()

    seeds = [
        Task(task_type=TaskType.NOOP, payload={"delay_seconds": 120}, timeout_seconds=2),
        Task(task_type=TaskType.NOOP),
    ]
    started = time.monotonic()
    outcome = run_coordinator(ctx, pool, listener, [], progress, seed_tasks=seeds)

    assert time.monotonic() - started < 15.0
    assert outcome == CoordinatorResult(interrupted=False, unfinished=0)
    assert progress.incomplete.timed_out == 1
    assert progress._tasks_completed == 1
    assert pool.size == 2
    assert pool.pids() != original_pids, "the hung worker should have been replaced"


@pytest.mark.slow
def test_hung_worker_replaced_other_workers_continue(tmp_path: Path) -> None:
    """3 workers, 1 hung task + 5 quick tasks.

    The quick tasks all complete while the hung worker is detected and replaced,
    and the pool ends the run at its original size — the regression guard for
    the old double replacement, which grew the pool.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])
    listener = start_listener(log_queue, tmp_path / "hung.log", "DEBUG")
    pool = _start_pool(ctx, 3)
    progress = _non_tty_progress()

    hung = Task(task_type=TaskType.NOOP, payload={"delay_seconds": 120}, timeout_seconds=2)
    quick = [Task(task_type=TaskType.NOOP) for _ in range(5)]
    started = time.monotonic()
    outcome = run_coordinator(ctx, pool, listener, [], progress, seed_tasks=[hung, *quick])

    assert time.monotonic() - started < 15.0
    assert outcome.unfinished == 0
    assert progress._tasks_completed == 5
    assert progress.incomplete.timed_out == 1
    assert pool.size == 3


# ---------------------------------------------------------------------------
# Integration: crash recovery, driven through the real run_coordinator
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_crash_after_heartbeat_is_redispatched_and_completed(tmp_path: Path) -> None:
    """A worker that dies mid-task has the task re-dispatched to its replacement.

    The first worker dequeues the task, announces it, then exits hard.  The
    crash sweep replaces it and puts the task back on the queue under the same
    id; the replacement is a normal worker and completes it.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])
    log_file = tmp_path / "crash.log"
    listener = start_listener(log_queue, log_file, "DEBUG")
    pool = _start_pool(ctx, 1, targets=[_crash_after_heartbeat_worker])
    progress = _non_tty_progress()

    outcome = run_coordinator(ctx, pool, listener, [], progress, seed_tasks=[Task(task_type=TaskType.NOOP)])

    assert outcome.unfinished == 0
    assert progress._tasks_completed == 1
    assert progress.incomplete.abandoned == 0
    assert "re-dispatching" in log_file.read_text()


@pytest.mark.slow
def test_poison_task_is_abandoned_rather_than_retried_forever(tmp_path: Path) -> None:
    """A task that kills every worker it reaches ends the scan instead of looping.

    Every worker the pool starts crashes on its first task.  The coordinator must
    give up after MAX_RETRIES re-dispatches.  Run in a thread with a join
    timeout so a regression fails the test instead of hanging the suite.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])
    listener = start_listener(log_queue, tmp_path / "poison.log", "DEBUG")
    pool = WorkerPool(lambda: _spawn(ctx, _crash_after_heartbeat_worker), logger=_POOL_LOG)
    pool.start(1)
    progress = _non_tty_progress()

    outcomes: list[CoordinatorResult] = []
    runner = threading.Thread(
        target=lambda: outcomes.append(
            run_coordinator(ctx, pool, listener, [], progress, seed_tasks=[Task(task_type=TaskType.NOOP)])
        ),
        daemon=True,
    )
    runner.start()
    runner.join(timeout=90.0)

    assert not runner.is_alive(), "coordinator never gave up on a task that crashes every worker"
    assert outcomes and outcomes[0].unfinished == 0
    assert progress.incomplete.abandoned == 1
    assert progress._tasks_completed == 0


# ---------------------------------------------------------------------------
# Regression guards: seed timeout, malformed child tasks, run outcome
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_seeded_tasks_use_configured_timeout(tmp_path: Path) -> None:
    """Seed ENUM_DIR tasks carry config.default_timeout_seconds, not the model default.

    Regression: the seed omitted timeout_seconds, so root tasks silently used the
    Task model's hardcoded 30 while every descendant used the configured value.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    seeds = build_seed_tasks(Config(start_dirs=[a, b], default_timeout_seconds=123))

    assert len(seeds) == 2
    assert all(t.task_type == TaskType.ENUM_DIR for t in seeds)
    assert all(t.timeout_seconds == 123 for t in seeds)
    assert {t.payload["path"] for t in seeds} == {str(a), str(b)}
    assert all(t.payload["depth"] == 0 for t in seeds)


@pytest.mark.unit
def test_seeded_tasks_empty_when_no_start_dirs() -> None:
    """No start dirs means no seed tasks, so the coordinator exits immediately."""
    assert build_seed_tasks(Config(start_dirs=[])) == []


@pytest.mark.integration
def test_malformed_child_task_is_dropped_and_scan_completes(tmp_path: Path) -> None:
    """A producer emitting an invalid new_tasks dict must not abort the whole scan.

    Task has extra="forbid"; unguarded, the ValidationError escaped run_coordinator,
    skipped temp cleanup, and ended the run.  It should now drop that one child.

    A result carrying the malformed child is injected for a task the registry
    really is tracking.  An untracked id would be dropped as a duplicate before
    its children were ever parsed, and the guard would go unexercised.  The
    worker's own result for the same task arrives later and is the duplicate.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])
    log_file = tmp_path / "malformed.log"
    listener = start_listener(log_queue, log_file, "DEBUG")
    pool = _start_pool(ctx, 1)
    progress = _non_tty_progress()

    seed = Task(task_type=TaskType.NOOP)
    result_queue.put(
        TaskResult(
            task_id=seed.task_id,
            task_type=TaskType.NOOP,
            status="ok",
            new_tasks=[{"task_type": "enum_dir", "payload": {}, "no_such_field": 1}],
        )
    )

    outcome = run_coordinator(ctx, pool, listener, [], progress, seed_tasks=[seed])

    assert outcome == CoordinatorResult(interrupted=False, unfinished=0)
    assert all(not w.is_alive() for w in pool.processes)
    assert "dropping malformed child task" in log_file.read_text()


@pytest.mark.integration
def test_clean_run_reports_no_unfinished_work(tmp_path: Path) -> None:
    """A normal scan reports interrupted=False and unfinished=0."""
    scan_root = tmp_path / "scan_root"
    scan_root.mkdir()
    (scan_root / "a.txt").write_text("hello")

    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()

    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [scan_root])
    listener = start_listener(log_queue, tmp_path / "clean.log", "WARNING")
    pool = _start_pool(ctx, 2)
    progress = _non_tty_progress()

    outcome = run_coordinator(ctx, pool, listener, [], progress)

    assert outcome.interrupted is False
    assert outcome.unfinished == 0


@pytest.mark.integration
def test_failed_task_is_reported_as_not_scanned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A task that returns an error shows up on the summary's "Not fully scanned" line.

    A missing start directory makes ENUM_DIR return status="error", which is
    the same path an access-denied folder takes.
    """
    task_queue: mp.Queue[object] = mp.Queue()
    result_queue: mp.Queue[object] = mp.Queue()
    log_queue: mp.Queue[object] = mp.Queue()
    stop_event = mp.Event()
    ctx = _make_ctx(task_queue, result_queue, log_queue, stop_event, [])
    listener = start_listener(log_queue, tmp_path / "failed.log", "WARNING")
    pool = _start_pool(ctx, 1)
    progress = _non_tty_progress()

    missing = Task(task_type=TaskType.ENUM_DIR, payload={"path": str(tmp_path / "does-not-exist"), "depth": 0})
    outcome = run_coordinator(ctx, pool, listener, [], progress, seed_tasks=[missing])

    assert outcome == CoordinatorResult(interrupted=False, unfinished=0)
    assert progress.incomplete.failed == 1
    assert "1 failed with an error" in capsys.readouterr().out
