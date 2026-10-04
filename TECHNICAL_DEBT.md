# Technical Debt

Known gaps we chose not to fix yet. Each entry says what is wrong, why it was deferred, where it lives, and a suggested fix. Remove an entry when it is fixed; don't mark it done.

## Orchestration

### A worker killed while holding a queue lock can hang the scan
- **What:** `mp.Queue` and `mp.Event` use cross-process locks. A worker that dies while holding one never releases it, and no sweep can recover it.
  - **Result-queue write lock (Linux/macOS only; scenarios 2a and 2b in the measurement README):** this is the measured risk. The write lock exists only on POSIX. A worker's feeder thread holds it for each send. If the worker dies mid-send, no worker's message reaches the coordinator again. `TaskStarted` never arrives, so tasks stay QUEUED and the registry never empties. A large message cut in half instead leaves the coordinator stuck in `recv_bytes()`.
  - **Task-queue read lock (all platforms; scenario 1):** a worker killed while idle in `task_queue.get()` starves every other worker. The lost-task sweep then abandons the remaining work: an incomplete scan, not a hang.
  - **`stop_event` (all platforms; scenario 3):** a worker killed inside `is_set()` makes teardown's `stop_event.set()` block forever.
- **Measured exposure (2026-10-04, Linux under WSL2, small text files on local ext4):**
  - **Per worker death:** 12 of 70 real scans hung, each with 5 SIGKILLs at random moments. That is about 4% per death (95% range about 2–6%) at both `fast` and `balanced`.
  - **Cause:** the coordinator reads only about 1,000–1,200 messages/s. Feeders queue behind the write lock, which some worker held 56–63% of the time.
  - **Hang type:** every hang was the small-message, lock-held case. The other two locks caused none in 350 kills.
  - **Windows:** a killed writer's partial message is discarded, and other workers' messages still arrive.
  - **Scripts and full results:** [docs/architecture/orchestration/measurements/queue-lock-hangs/](docs/architecture/orchestration/measurements/queue-lock-hangs/README.md). Rerun them after the fix.
- **Why deferred:** Accepted for now; to be fixed in the next release.
  - Exposure requires a worker death on Linux/macOS. Deadline kills are low-risk, because a worker silent for 2× its timeout has long since flushed its messages.
  - The realistic trigger is a native crash on a poison file. Only a few mature libraries can crash that way: `lxml`, py7zr's codecs (`pyppmd`, `inflate64`, `bcj`, `brotli`, `pycryptodomex`), stdlib `zlib`/`lzma`/`bz2`, and `charset-normalizer`. `pypdf` is pure Python.
  - Slower storage, such as network shares, lowers coordinator saturation and the rate. That case is unmeasured.
- **Where:** `orchestration/context.py` (the shared queues and event), `orchestration/worker/_loop.py`, `orchestration/coordinator.py` (`_drain`, `_teardown`), `run.py` (queue creation).
- **Suggested fix:**
  - Give each worker a private `mp.Pipe` for results, progress and logs. The coordinator closes its copy of the child end and waits on all pipes with `multiprocessing.connection.wait()`. A dead writer then shows up as `EOFError`/`OSError` instead of a held lock. Task scheduling is unchanged.
  - Replace `stop_event` with a lock-free `mp.RawValue` flag.
  - Leave the task-queue read lock unless measurements show it matters; fixing it means push scheduling.

### Crash blame can pick the wrong archive member
- **What:** When a batch's worker crashes, the coordinator blames `current_item` — the member the worker last reported starting. `mp.Queue` sends through a background feeder thread, so a hard crash can lose the last `item_done` / `item_started` messages. The coordinator can then drop a member that actually finished (losing its findings) or find no member to blame and fall back to `redispatch`, which abandons the rest of the batch after `MAX_RETRIES`.
- **Why deferred:** Needs a design decision; only native crashes (segfaults) trigger it.  Quite rare, could only really happen if a worker hard-crashed within milliseconds of finishing the prior archive member.
- **Where:** `orchestration/coordinator.py` (`HealthMonitor.tick`, crash sweep), `orchestration/registry.py` (`requeue_remaining`).
- **Suggested fix:** Don't drop a suspect on its first crash. Requeue it with a retry budget similar to on-disk files, and drop it only after it's budget is spent.

### Batches multiply decompression CPU for low-R formats
- **What:** `batch_count()` creates at least one batch per worker. Each batch re-decompresses the part of the archive before it, about (K−1)/2 extra passes in total. For gzip this is small next to scanning (R ≈ 200). For 7z through py7zr, R is only about 8–10, so 8 batches add roughly 40% CPU.
- **Why deferred:** Wall-clock time still improves, and the right answer depends on measurements on real archives.
- **Where:** `orchestration/worker/_enum_archive.py` (`batch_count`), `archives.max_batch_mb`.
- **Suggested fix:** Use fewer batches when R is low, for example a per-format floor below `n_workers` for solid 7z. Measure with `testdata/archives/bench_member_access.py`.

### ETA counts tasks, not files
- **What:** The progress display estimates time remaining from completed tasks. An archive batch is one task covering many members, so the ETA is rough for archive-heavy scans.
- **Where:** `orchestration/progress.py` (`_compute_eta`).
- **Suggested fix:** Count `item_done` messages as completed work and keep a member-level pending count in the registry.

### Concurrent batches from one archive on slow storage
- **What:** Batches from one archive read it at the same time. Readers never block each other, but on a spinning disk or slow network share the extra seeking may cost throughput.
- **Why deferred:** Not observed; SSDs are unaffected.
- **Suggested fix:** If it shows up, cap how many batches from one archive run at once.

## Archives

### Archive members are matched by extension only
- **What:** Members are never MIME-detected. A member with no extension, or a wrong one, is never scanned, even if its content is a supported type. Files on disk fall back to MIME detection.
- **Why deferred:** Detecting MIME would mean sniffing the start of each unknown member during extraction, so the scan task, not enumeration, would decide what is scanned.
- **Where:** `orchestration/worker/_enum_archive.py` (check 8), `filehandlers.select_handler()`.
- **Suggested fix:** Accept unknown-extension members at enumeration, sniff the first few KB in memory inside `extract_members()`, and skip the member there if its MIME type has no handler.

### Windows reserved device names in member names
- **What:** On Windows before 11, a member named `con.txt`, `nul.txt`, `aux.txt` or `com1.txt` can resolve to a device instead of a file. Extracting `con.txt` could write the member's bytes to the console and then block when scanning it. Windows 11 treats these as ordinary files.
- **Where:** `orchestration/worker/_enum_archive.py` (check 2).
- **Suggested fix:** Reject member basenames whose stem is a reserved device name.

### A corrupt member in a solid 7z folder fails the rest of that folder
- **What:** After a CRC error, the batch is requeued without the bad member. py7zr still re-checks the bad member's CRC while skipping past it, so the retry fails again before reaching later members in that folder.
- **Why deferred:** Library behavior. Data after a corrupt point in one compressed stream usually can't be decoded anyway.
- **Where:** `archivehandlers/_7z.py`.

## Configuration

### `"all"` is stored literally instead of expanded
- **What:** `include_exts`, `include_mime`, `data_handlers`, `archives.formats` and `results.formats` keep the literal string `"all"`, and every consumer tests `"all" in ...`. Expanding `"all"` to the concrete supported list in the config validators would remove those special cases. The validator for `include_exts` also compares case-sensitively, so `".PDF"` is rejected as unknown even though matching is case-insensitive.
- **Why deferred:** Bigger change than needed for the current release.
- **Where:** `models/config.py` (field validators), consumers in `orchestration/worker/` and `run.py`.
- **Suggested fix:** Expand `"all"` and lowercase extensions in the validators; drop the `"all" in ...` checks from the consumers.

## Tooling

### Files that `ruff format` would still change
- **What:** `src/piidigger/run.py` (Python 3.14 `except A, B:` style), `src/piidigger/archivehandlers/__init__.py`, `src/piidigger/filehandlers/pdf.py` and `tests/test_archives.py` were already unformatted before the archive-batching work.
- **Suggested fix:** Run `uv run ruff format src/ tests/` in a commit of its own.

### `mkdocs build --strict` fails on links to source files
- **What:** Several docs link to files outside `docs/` (source files, workflows). mkdocs warns on each, so `--strict` fails.
- **Suggested fix:** Link to the GitHub URL instead, or let mkdocstrings render the code where the link is to Python source.
