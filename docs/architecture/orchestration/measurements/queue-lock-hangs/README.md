# Queue-Lock Hang Measurements

These scripts measure how often a worker death hangs a scan when the worker held a cross-process queue lock. They back the "A worker killed while holding a queue lock can hang the scan" entry in [TECHNICAL_DEBT.md](../../../../../TECHNICAL_DEBT.md). Rerun them after the fix: M1 and M3 should report zero hangs.

## Why a dead worker can hang the scan

Workers share three multiprocessing objects with the coordinator and with each other: `task_queue`, `result_queue` and `stop_event`. Each one uses a cross-process lock internally:

- **`Queue._rlock`** (all platforms). `get()` holds it for as long as a worker waits for an item.
- **`Queue._wlock`** (Linux and macOS only; it is `None` on Windows). The queue's background feeder thread holds it for each `send_bytes()`. If the pipe is full, the feeder holds it until the reader makes room.
- **The `Event`'s internal lock** (all platforms). `is_set()` and `set()` hold it for a few microseconds.

The operating system does not release these locks when the holder dies. A worker killed while holding one leaves it held for the rest of the run. Timeouts and health sweeps cannot recover a lock that a dead process holds.

A write cut off halfway is a second problem. A message larger than the OS's atomic-write size (4 KiB on Linux, 512 B on macOS) goes out in pieces. If the writer dies partway through, the reader waits for bytes that never come. `Queue.get(timeout=...)` does not protect against this: the timeout covers waiting for data to *start*, not reading the rest of a message.

## Hang scenarios

| ID | Platforms | What has to happen | Outcome |
|---|---|---|---|
| **1** | All | A worker is killed while idle in `task_queue.get()`, and it is the idle worker currently holding `_rlock`. The others are waiting for `_rlock`; killing one of those is harmless. | No worker can take another task. The lost-task sweep redispatches outstanding tasks up to `MAX_RETRIES`, then abandons them. The scan exits as incomplete within seconds. **Not a hang.** |
| **2a** | Linux, macOS | A worker dies while its feeder holds `result_queue._wlock`, sending a small message. | The message is either whole or never written, but the lock stays held. No worker's message reaches the coordinator again. The coordinator keeps looping and sweeping in `_drain()`. `TaskStarted` and `WorkerReady` never arrive, so tasks stay QUEUED and the registry never empties. **Hang**: the display stays up with frozen counts. |
| **2b** | Linux, macOS | A worker dies partway through writing a message larger than the atomic-write size. | The coordinator's main thread blocks in `recv_bytes()`, waiting for the rest. Sweeps and display updates stop. **Hang.** |
| **3** | All | A worker dies inside `stop_event.is_set()`. | Nothing shows until teardown. Then `_teardown()` blocks in `stop_event.set()`, before `pool.join()` and `_flush_sinks()`. **Hang at exit**; output files may not be flushed. |

The log queue has the same 2a and 2b exposure. There the damage is smaller: logging stalls, and `stop_listener()` gives up after a timeout. `pool.join()` escalates to `terminate()`/`kill()` for workers whose feeders are blocked.

## How workers die

| Source | When it happens | Where the worker usually is |
|---|---|---|
| **A. Deadline kill** | `HealthMonitor` calls `pool.replace()`, which uses `terminate()`, for a RUNNING task silent for 2× its timeout (60 s by default). | Stuck inside a handler. Its feeder finished sending long ago, so it holds no lock. |
| **B. Native crash** | A segfault or abort in a C extension on a poison file. Each poison file gets up to 4 attempts (1 + `MAX_RETRIES`). | Inside a handler, often early in the file. The feeder may still be sending earlier messages. |
| **C. OOM or outside kill** | The Linux OOM killer, a user, or EDR/antivirus. | Anywhere. The OOM killer prefers the largest process, usually one in the middle of a big file. |
| **D. Teardown escalation** | `pool.join()` → `terminate()`/`kill()` at the end of a run. | After `stop_event.set()`, when results no longer matter. Harmless. |

`KeyboardInterrupt` is not a kill: workers catch it and exit cleanly.

**The late-result race** is the one way source A becomes dangerous:
1. A task finishes just inside its deadline.
2. The worker puts the result in its feeder buffer and goes back to `stop_event.is_set()` and `task_queue.get()`.
3. A sweep kills the worker before the result reaches the coordinator.

The worker is then outside a handler, and its feeder is busy sending the result. That exposes it to scenarios 1, 2 and 3. The window is the result's delivery delay, normally milliseconds.

## Measurements

Each measurement answers one question. Together they turn "could this happen?" into a rate.

| | Question | Method | What it can and cannot show |
|---|---|---|---|
| **Hazard repro** (`mp_hazards.py`) | Do the lock hazards exist in this Python, on this OS? | Plain stdlib processes. Each scenario kills a process at the dangerous point, then checks whether the survivors can still make progress. | Confirms the mechanism for scenarios 1, 2 and 3 per platform. Says nothing about how often PIIDigger reaches those points. |
| **M1** (`m1_crash_after_result.py`) | If a worker crashes right after sending a result, how often does the scan hang, and how long is the danger window? | The real coordinator, pool and worker loop, with the NOOP handler swapped for one that injects the fault. A worker returns a result of a chosen size, then calls `os.abort()` a set delay into its next task. This models a poison file taken right after a large directory listing. Exactly one crash per trial. | The worst realistic timing for scenario 2b, plus 2a in the small-result control. Gives the window as a function of message size and coordinator load. It does not show how often real scans produce large messages or crash at those moments. |
| **M2** (`m2_sample_scan.py`) | In a real scan, how often does some worker hold the result write lock, and how big are real messages? | A real `run_scan` on a realistic tree. A thread in the coordinator process tries a non-blocking acquire of each `_wlock` about every 1 ms, and wraps `_recv_bytes` to record message sizes. | **Lock-held fraction ÷ worker count** approximates the chance that a worker dying at a random moment is the current holder (scenario 2a). The message sizes show whether 2b matters in practice. It is an inference, not an observed hang. |
| **M3** (`m3_random_kill_scan.py`) | How often does a worker death at a random moment hang a real scan? | A real `run_scan` on an 11,525-file subset. A thread SIGKILLs 5 random workers at random moments per trial; a watchdog reports a hang and classifies it. If each kill hangs with probability p, then survival = (1 − p)⁵, so p comes from the survival rate. | The direct, end-to-end rate for kill sources B and C. It does not model deadline kills (A), whose timing is not random. |

M2 and M3 are independent ways to get the same number, and they agree (see Results). The hazard repro and `mp_pipe_check.py` also check the proposed fix outside PIIDigger, before any code changes.

## Scripts

| Script | What it does |
|---|---|
| `mp_hazards.py` | Stdlib-only repro of the three lock hazards, outside PIIDigger. Kills a process holding `Queue._rlock`, mid-`send_bytes()`, or inside `Event.is_set()`. |
| `mp_pipe_check.py` | Stdlib-only check of the proposed fix. A worker killed mid-send on a private `mp.Pipe` raises `EOFError`/`OSError` instead of hanging, and siblings are unaffected. |
| `m1_crash_after_result.py` | **M1.** Drives the real `run_coordinator`, `WorkerPool` and `worker_loop`. One worker sends a result, then calls `os.abort()` a set delay into its next task. One trial per process. |
| `run_m1.sh` | Runs the M1 grid: result size × crash delay, a small-result control, and a saturated coordinator. |
| `m2_make_data.py` | Builds a 72,545-file, 1.2 GB tree shaped like a file share, plus copies of `testdata/`. |
| `m2_sample_scan.py` | **M2.** Runs a real `run_scan`. Samples the result and log queue write locks every ~1 ms and records the size of every result message. |
| `m3_random_kill_scan.py` | **M3.** Runs a real `run_scan` and SIGKILLs 5 random workers at random moments. A watchdog reports a hang and where the coordinator is stuck. |
| `run_m3.sh` | Runs 40 M3 trials at `fast` and 30 at `balanced`. |
| `summarize.py` | Summarizes M1 or M3 result files. For M3 it derives the per-kill hang rate and a 95% range. |

## Running

Run on Linux or macOS; WSL2 works. The write-lock hazard does not exist on Windows. Keep the clone, data and output on the native file system, not `/mnt/c`. Faster storage raises worker throughput and therefore exposure.

```bash
git clone <repo> ~/pd-measure && cd ~/pd-measure && uv sync --extra dev
D=docs/architecture/orchestration/measurements/queue-lock-hangs
PY=.venv/bin/python

$PY $D/mp_hazards.py          # ~2 min
$PY $D/mp_pipe_check.py       # ~10 s

bash $D/run_m1.sh             # ~45-60 min; writes m1_results.jsonl
$PY $D/summarize.py m1 m1_results.jsonl

$PY $D/m2_make_data.py ~/m2data testdata
$PY $D/m2_sample_scan.py ~/m2data ~/m2out balanced
$PY $D/m2_sample_scan.py ~/m2data ~/m2out fast
$PY $D/m2_sample_scan.py ~/m2data ~/m2out fast plain   # uninstrumented timing

# M3 scans an 11,525-file subset of the M2 tree
mkdir -p ~/m3data/share
for d in $(seq -w 0 9); do cp -r ~/m2data/share/dept0$d ~/m3data/share/; done
cp -r ~/m2data/dense ~/m3data/ && cp -r ~/m2data/docs/copy0 ~/m3data/docs
bash $D/run_m3.sh             # ~50 min; writes m3_results.jsonl
$PY $D/summarize.py m3 m3_results.jsonl
```

`run_m3.sh` times its kills for that subset: about 21 s per scan at `fast` and 31 s at `balanced`. With different data, first run one trial with zero kills. Then scale the kill timings and timeouts in the script to fit.

Clean up afterwards with `rm -rf ~/pd-measure ~/m2data ~/m3data ~/m2out ~/m3out`. Check `/dev/shm` for leftover `sem.mp-*` files.

After the fix, update the hang classification in M1 and M3, and drop M2. All three read `result_queue._wlock`, which goes away with per-worker pipes.

## Results (2026-10-04)

Measured on Linux under WSL2: Ubuntu, Python 3.14.4, 16 logical cores, local ext4.

### Hazard repro (`mp_hazards.py`)

| Scenario | Hazard | Windows | Linux |
|---|---|---|---|
| 1 | Kill the process holding `Queue._rlock` in `get()` | Survivors never receive another item | Same |
| 2b | Kill a writer mid-`send_bytes()` of a 4 MB message | Partial message dropped; sibling's message arrives | Reader stuck in `recv_bytes()` |
| 3 | Kill a process inside `Event.is_set()` | `set()` hung in 7/20 trials | `set()` hung in 11/20 trials |

### M1: crash after a result

"Crash ms" is the time from handing off the result to `os.abort()`. A hang showing the coordinator stuck in `recv_bytes()` counts as 2b; one with the write lock still held counts as 2a. Inside the window, the trials that did not hang crashed before the feeder started writing, so the result was rerun.

| Previous message | Crash ms | Hung | Scenario |
|---|---|---|---|
| Small result | ~0.7 | 2/30 | 2a |
| Small result | 2.5–5.5 | 0/60 | — |
| 218 KB (~1,000-file directory) | ~1 | 10/10 | 7× 2b, 3× 2a |
| 218 KB | ≥2.4 | 0/80 | — |
| 2.2 MB (~10,000 files) | 6–7 | 28/30 | 2b |
| 2.2 MB | ≥10 | 0/60 | — |
| 2.2 MB, saturated coordinator | 11 / 16.5 | 7/10, 5/10 | 2b |
| 2.2 MB, saturated coordinator | ≥27 | 0/40 | — |
| 11 MB (~50,000 files) | 40–42 | 62/70 | 61× 2b, 1× 2a |
| 11 MB | ≥50 | 0/20 | — |

The window is roughly the time the coordinator takes to read the message. Saturation doubles it. With an 11 MB result, the next task cannot start for about 40 ms: the feeder holds the GIL while it pickles the message. So the delays from 0 to 30 ms all land at 40–42 ms.

### M2: lock occupancy in a real scan

| Preset | Workers | Scan time | Result write lock held | Log write lock held | Messages > 4 KiB |
|---|---|---|---|---|---|
| `balanced` | 12 | 107.9 s | 55.7% | 0.04% | 12 of 108,908 |
| `fast` | 16 | 92.2 s | 63.1% | 0.01% | 12 of 108,912 |

The largest message was 510 KB. The uninstrumented `fast` scan took 87.3 s, so the sampler costs about 5–7%. The coordinator reads about 1,000–1,200 messages/s, and workers queue behind the write lock.

So in real scans 2a is the risk, not 2b. Nearly every message is small, and the lock is held most of the time.

### M3: random worker kills in a real scan

| Preset | Trials | Hung | Per-kill hang rate (95% range) |
|---|---|---|---|
| `fast` | 40 | 6 | 3.2% (1.5–6.6%) |
| `balanced` | 30 | 6 | 4.4% (2.0–8.9%) |
| Combined | 70 | 12 | 3.7% (2.1–6.3%) |

All 12 hangs were scenario 2a: the result write lock stayed held, and the coordinator kept sweeping in `_drain()` with no message able to arrive. There were no 2b, 1 or 3 outcomes in 350 kills: all 58 other trials exited with code 0 and tore down cleanly. By the rule of three, each of those is under about 1% per kill. M2 predicts the same 2a rate independently: lock-held fraction divided by worker count is 3.9–4.6%.

## What the numbers mean

| How a worker dies | Hang chance on Linux/macOS |
|---|---|
| **B. Native crash on a poison file** | ~4% per crash. With up to 4 attempts per file, about 14% per poison file. |
| **C. OOM or outside kill** | ~4% per kill. |
| **A. Deadline kill** | Not measured; much lower by mechanism. A worker silent for 60 s finished sending long ago, and the longest lock contention in M2 was about 8 s. What's left is the late-result race. |
| **Any death on Windows** | No scenario 2 exposure. Scenarios 1 and 3 apply, but neither showed up in 350 Linux kills. |

The native code that can crash on a poison file is `lxml` (docx/xlsx), py7zr's codecs (`pyppmd`, `inflate64`, `bcj`, `brotli`, `pycryptodomex`), stdlib `zlib`/`lzma`/`bz2`, and `charset-normalizer` (compiled with mypyc). `pypdf` is pure Python and cannot segfault.

Caveats:

- These numbers come from the high-exposure end: small text files on fast local storage. Slower storage, such as a network share, throttles workers, lowers coordinator saturation, and should lower the rate. That case is unmeasured.
- Recovering a 2a hang with Ctrl-C is untested. Reading `_teardown()`, it should flush partial results, because `pool.join()` escalates to killing the blocked workers.
- The same coordinator limit (~1,000–1,200 messages/s) also caps small-file scan speed. Going from 12 to 16 workers only cut the M2 scan from about 108 s to 92 s.
