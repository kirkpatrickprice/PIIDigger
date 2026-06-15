# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Active Refactor: Read This First

This repository is mid-way through a **clean-slate 2.0 architectural rewrite** on the `refactor` branch. The entire orchestration layer is being replaced. Before touching any code, read:

- **[docs/refactor/ARCHITECTURE_REDESIGN.md](docs/refactor/ARCHITECTURE_REDESIGN.md)** — what we're building and why (design is locked)
- **[docs/refactor/IMPLEMENTATION_CHECKLIST.md](docs/refactor/IMPLEMENTATION_CHECKLIST.md)** — what phase we're in and what's left to do

The `refactor` branch is the working branch. `main` is the 1.x release baseline.

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

### Target module layout (2.0 — being built now)

```
src/piidigger/
├── cli/                    # Click entry point; no business logic
│   ├── main.py             # click.group(); entry point replaces piidigger.piidigger:main
│   └── commands/
│       ├── scan.py         # `piidigger scan`
│       └── config.py       # `piidigger config generate|validate`
├── models/                 # All Pydantic data models
│   ├── config.py           # Config (replaces classes.Config getter-soup)
│   ├── tasks.py            # Task, TaskResult, TaskType, SHUTDOWN
│   ├── payloads.py         # Typed per-task-type payloads
│   └── results.py          # ResultRecord (with lineage fields)
├── protocols.py            # DataHandler, FileHandler, OutputSink, ScannableItem protocols
├── orchestration/          # All multiprocessing-aware code (new; strict mypy)
│   ├── context.py          # WorkerContext (frozen dataclass — see note below)
│   ├── worker.py           # worker_loop, DISPATCH table
│   ├── coordinator.py      # fan-out loop, pending counter, deadline monitor
│   ├── logging_setup.py    # QueueHandler / QueueListener helpers
│   ├── progress.py         # rich.Live two-panel display
│   └── sources.py          # FilesystemItem; ArchiveMemberItem (Phase 5)
├── datahandlers/           # PII matchers — implement DataHandler protocol
├── filehandlers/           # File readers — implement FileHandler protocol
├── outputhandlers/         # Output sinks — implement OutputSink protocol
└── run.py                  # run_scan(config: Config) -> int  (testable core)
```

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
- **Pydantic v2** for all data models (`Task`, `TaskResult`, `Config`, `ResultRecord`, payload types).
- **`dataclass(frozen=True)`** for `WorkerContext` only — it holds `mp.Queue`/`mp.Event` which Pydantic cannot meaningfully validate. Document the reason at the class definition.

### Multiprocessing / pickling (Windows `spawn`)
- `WorkerContext` must contain only pickle-safe members. `mp.Queue`, `mp.Event`, and a plain Pydantic `Config` are safe. A live `logging.Logger` or `rich.Console` is **not** — build those inside each process.
- Workers build their own logger via `build_worker_logger(ctx.log_queue)`. Never pass a `Logger` across the process boundary.

### Protocols
All business-logic contracts live in `protocols.py`. Handlers must not import `multiprocessing`, queues, or loggers.

---

## Architecture: How It Works (2.0)

One coordinator (main process) feeds N identical workers through a single task queue. Workers return `TaskResult` objects containing `new_tasks` (fan-out), `findings` (PII matches), and `counters` (progress). The coordinator enqueues the new tasks, routes findings to output sinks, and tracks `pending` (outstanding task count). When `pending == 0`, the run is done.

Termination is a property of the work set — no SENTINEL chains. Adding a task type adds one entry to the `DISPATCH` dict and one handler function; the coordinator and worker are unchanged.

The coordinator also owns the `rich.Live` progress display and monitors worker heartbeats. A worker that exceeds its deadline is terminated and replaced; the coordinator synthesizes a `status="timeout"` result so `pending` decrements correctly.

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
