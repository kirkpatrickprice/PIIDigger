# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Refactor Status

The 2.0 architectural rewrite (Phases 0-5) is complete on the `refactor` branch, plus a
post-completion reliability hardening pass on the coordinator/worker pipeline (task registry,
worker pool, health-sweep monitor, shutdown correctness). It has not yet merged to `main`,
which remains the 1.x release baseline.

Before touching orchestration code, read:

- **[docs/architecture/orchestration/coordinator-worker-pipeline.md](docs/architecture/orchestration/coordinator-worker-pipeline.md)** — the coordinator/worker pipeline as it exists today
- **[docs/architecture/archives/archive-handling.md](docs/architecture/archives/archive-handling.md)** — archive (zip/7z/tar) design as it exists today

`docs/refactor/` holds the original design rationale and phase-by-phase build record — useful
historical context (its own [README](docs/refactor/README.md) explains why), but not the place
to look for current behavior; the two docs above are the current source of truth where they
differ.

---

## Commands

All commands use `uv`. Install dependencies first with `uv sync --extra dev`.

```bash
# Run the CLI
uv run piidigger scan
uv run piidigger --help

# Lint (zero violations required before committing)
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type checking
uv run mypy src/

# Run all tests
uv run pytest tests/ -v

# Run a single test
uv run pytest tests/path/to/test_file.py::test_function_name -v

# Run by marker
uv run pytest tests/ -m "not slow and not e2e" -v   # fast tests only
uv run pytest tests/ -m slow -v                      # timeout/reliability tests
uv run pytest tests/ -m e2e -v                        # full scan + baseline

# Coverage
uv run pytest tests/ --cov=src/piidigger --cov-report=term-missing

# Docs (local preview)
uv run mkdocs serve
```

---

## Project Structure

### Current module layout (2.0 implementation complete)

```
src/piidigger/
├── cli/                    # Click entry point; no business logic
│   ├── main.py             # click.group(); entry point replaces piidigger.piidigger:main
│   └── commands/
│       ├── scan.py         # `piidigger scan`
│       ├── config.py       # `piidigger config generate|validate`
│       └── inspect.py      # `piidigger inspect` (MIME types, data handlers, CPU count, etc.)
├── models/                 # All Pydantic data models
│   ├── base.py             # PiiDiggerModel — shared BaseModel with extra="forbid"
│   ├── config.py           # Config (replaces classes.Config getter-soup)
│   ├── tasks.py            # Task, TaskResult, TaskProgress, TaskType, SHUTDOWN
│   ├── payloads.py         # Typed per-task-type payloads
│   ├── archive.py          # MemberInfo (archive member metadata)
│   └── results.py          # ResultRecord (with lineage fields)
├── protocols.py             # DataHandler, FileHandler, OutputSink, ScannableItem, ArchiveHandler protocols
├── exceptions.py            # ArchiveReadError
├── orchestration/          # All multiprocessing-aware code (strict mypy)
│   ├── context.py          # WorkerContext (frozen dataclass — see note below)
│   ├── worker/              # package: worker_loop, DISPATCH table, split by task type
│   │   ├── _loop.py         # worker_loop, DISPATCH, _cleanup_temp_workspace
│   │   ├── _enum_dir.py     # handle_enum_dir
│   │   ├── _enum_archive.py # handle_enum_archive_members
│   │   ├── _scan_file.py    # handle_scan_file
│   │   ├── _scan_archive_members.py  # handle_scan_archive_members (one batch of members)
│   │   └── _reporter.py     # ProgressReporter — TaskProgress for long tasks
│   ├── coordinator.py      # fan-out loop, HealthMonitor (deadline/crash/lost-task sweeps)
│   ├── registry.py         # TaskRegistry, TaskRecord — the outstanding-work record
│   ├── pool.py              # WorkerPool, spawn_worker — process lifecycle
│   ├── logging_setup.py    # QueueHandler / QueueListener helpers
│   ├── progress.py         # rich.Live two-panel display
│   ├── secure_delete.py    # 2-pass overwrite + fsync + unlink for extracted archive members
│   ├── sinks.py            # GuardedSink — logs a failing OutputSink once and stops writing to it
│   └── sources.py          # FilesystemItem (archive_path/member_path kwargs cover archive members too)
├── archivehandlers/         # ArchiveHandler implementations: zip, 7z, tar (+ compressed tar variants)
├── datahandlers/           # PII matchers — implement DataHandler protocol (pan, email implemented; phonenum, trackdata are stubs)
├── filehandlers/           # File readers — implement FileHandler protocol
├── outputhandlers/         # Output sinks — implement OutputSink protocol
└── run.py                  # run_scan(config: Config) -> int  (testable core)
```

See [Coordinator/Worker Task Pipeline](docs/architecture/orchestration/coordinator-worker-pipeline.md) and [Archive Handling](docs/architecture/archives/archive-handling.md) for the architecture behind this layout.

### Legacy modules (being deleted by end of refactor)

`classes.ProcessManager`, `queuefuncs.py`, `filescan.py`, `piidigger.py` (worker functions), `globalvars.SENTINEL`. Do not add new code to these files.

---

## Code Standards

### Naming — non-negotiable
- Functions / methods / variables / modules: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_CASE`

The legacy `src/piidigger/**` tree is exempted from ruff's `N` ruleset until the Phase 0 rename. New packages (`orchestration/`, `models/`, `cli/`) are **not** exempted and must be born snake_case.

### Type hints
- Use `X | None` not `Optional[X]`; use `list[X]` not `List[X]` (Python 3.14+)
- `orchestration.*` is held to `mypy --strict`. All other packages are currently exempted (see `pyproject.toml [[tool.mypy.overrides]]`). Delete a module from the ignore list as you add full type coverage.

### Models
- The deciding question is **whether any field's value originates outside our own code**.
- **Pydantic v2** when it does: `Config` (TOML), `Task` / payload types (filesystem metadata), `TaskResult`, `TaskProgress` and `ResultRecord` (file content).
- **`dataclass`** when every field is a value we generated ourselves: `TaskStarted`, `WorkerReady`, `ShutdownSentinel`, `CoordinatorResult`, `TaskRecord`, `SweepResult`, and `WorkerContext` (which also holds `mp.Queue`/`mp.Event`, which Pydantic cannot meaningfully validate). Crossing the process boundary is not the test — `TaskStarted` crosses it and is still a dataclass.
- Use `frozen=True` unless the object is mutated in place (e.g. `TaskRecord`). Use `slots=True` for high-volume types.
- Document the reason for the choice at the class definition.

### Multiprocessing / pickling (Windows `spawn`)
- `WorkerContext` must contain only pickle-safe members. `mp.Queue`, `mp.Event`, and a plain Pydantic `Config` are safe. A live `logging.Logger` or `rich.Console` is **not** — build those inside each process.
- Workers build their own logger via `build_worker_logger(ctx.log_queue)`. Never pass a `Logger` across the process boundary.

### Protocols
All business-logic contracts live in `protocols.py`. Handlers must not import `multiprocessing`, queues, or loggers.

---

## Architecture: How It Works (2.0)

One coordinator (main process) feeds N identical workers through a single task queue. Workers return `TaskResult` objects containing `new_tasks` (fan-out), `findings` (PII matches), and `counters` (progress). The coordinator enqueues new tasks, routes findings to output sinks, and tracks outstanding work in a `TaskRegistry` (`orchestration/registry.py`) — a task is *registered* when enqueued and *retired* when its outcome is accounted for. The run ends when the registry is empty.

Termination is a property of the work set — no SENTINEL chains. Adding a task type adds one entry to the `DISPATCH` dict and one handler function; the coordinator and worker are unchanged.

The coordinator also owns the `rich.Live` progress display and a `HealthMonitor` that runs three sweeps every second regardless of queue traffic: a deadline sweep (terminates and replaces a worker that has exceeded its task's deadline), a crash sweep (replaces a worker that died unprompted and redispatches its task), and a lost-task sweep (recovers a task whose worker died before it could send a heartbeat at all — see [coordinator-worker-pipeline.md](docs/architecture/orchestration/coordinator-worker-pipeline.md) for the full mechanics). Worker processes and their lifecycle are owned by `WorkerPool` (`orchestration/pool.py`).

---

## Architecture Documentation Standards

When writing architecture docs, follow `.github/instructions/architecture.instructions.md`:

- Use `docs/templates/architecture-document-template.md` as the starting point
- Mermaid diagrams: subgraphs with emoji icons, consistent CSS classes (`coreService`, `protocol`, `component`, etc.)
- Writing style: present tense, active voice, sentences under 25 words
- Avoid the words: "ensure", "comprehensive", "strict", "rigorous", "well-defined", "effective"
- File location: `docs/architecture/{domain}/{service-name}.md` (kebab-case)

---

## Known Latent Bugs (crash only on error paths — safe to leave until Phase 0/3)

| Location | Bug | Fix |
|---|---|---|
| [classes.py:66,110](src/piidigger/classes.py) | `globalfuncs.errorCodes[...]` — `errorCodes` lives in `globalvars`, not `globalfuncs`; crashes on invalid-config or missing start-dir path | Move reference to `globalvars.errorCodes` |
| [piidigger.py:288](src/piidigger/piidigger.py) | `errorCodes['unknown']` — key doesn't exist; correct key is `'unknownError'` | Change key string |

Both are resolved as part of the `Config` rewrite in Phase 3. One-line patches are possible in Phase 0 if needed for testing.

---

## Key Configuration

- **Python**: 3.14+
- **Package manager**: `uv`
- **Line length**: 120 (ruff; E501 ignored)
- **Entry point** (current 1.x): `piidigger.piidigger:main` — changing to `piidigger.cli.main:cli` in Phase 3
- **`pyproject.toml` pytest markers** need `slow` and `e2e` added in Phase 0 (currently only `datahandlers`, `filehandlers`, `unit`, `utils` are declared)
