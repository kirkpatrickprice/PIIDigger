# Architecture Refactor Documentation

**Branch**: `refactor`
**Status**: Refactor complete — this folder is retained as historical reference only
**Last Updated**: 2026-07-06
**Target release**: 2.0.0

> **This is a historical record, not a living spec.** Every phase described below (0-5) is implemented, tested, and merged into the `refactor` branch. These documents capture the design rationale and decisions made along the way — useful for understanding *why* the architecture looks the way it does, but not the place to look for the current state of the code. For that, see:
>
> - [docs/architecture/orchestration/coordinator-worker-pipeline.md](../architecture/orchestration/coordinator-worker-pipeline.md) — coordinator/worker mechanics as they exist today
> - [docs/architecture/archives/archive-handling.md](../architecture/archives/archive-handling.md) — archive (zip/7z/tar) design as it exists today
> - [docs/reference/extending.md](../reference/extending.md) — how to add a handler
> - `CLAUDE.md`'s module layout — the current package structure

---

## Quick Navigation

| Document | Purpose | Start here when… |
|---|---|---|
| **[ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md)** | Original design: goals, module layout, all 6 phases, open decisions | You want the original design rationale for the coordinator/worker system |
| **[IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md)** | Per-phase task list with exit criteria — the authoritative build-progress record | You want to see exactly what was done, phase by phase |
| **[TESTING_STRATEGY.md](./TESTING_STRATEGY.md)** | Test structure, fixtures, examples, coverage targets | You're writing tests or reviewing test coverage |
| **[CURRENT_ISSUES.md](./CURRENT_ISSUES.md)** | Historical record of 1.x problems and their 2.0 dispositions | You want context on why a decision was made |
| **[ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md)** | Original ZIP-only archive design (superseded — see below) | You want the earliest archive-support rationale |
| **[ADR-multi-format-archives.md](./ADR-multi-format-archives.md)** | Design record for generalizing archive support beyond ZIP | You want to understand why the `ArchiveHandler` registry pattern exists |
| **[PHASE5_PLAN.md](./PHASE5_PLAN.md)** | Pre-coding decision record closing ZIP_HANDLING_PLAN's open questions | You want the detailed rationale behind secure deletion, temp isolation, CLI flags |
| **[TAR_HANDLING_PLAN.md](./TAR_HANDLING_PLAN.md)** | Design record for adding tar (+ compressed variants) via the same registry | You want the rationale for tar's compound-extension detection |

---

## Architecture at a Glance

**Problem**: Tightly-coupled SENTINEL-based process orchestration — hangs, deadlocks, impossible to extend, untestable.

**Solution**: Single coordinator feeding a pool of identical workers through one task queue.

```
Coordinator (main process)
  seeds tasks → task_queue → Worker pool (N identical workers)
  result_queue ← TaskResult (findings + new_tasks + counters)
  ↓
  fans out new tasks, routes findings to output sinks,
  updates rich.Live progress display,
  detects completion (pending == 0)
```

**Key properties:**
- Adding a task type = one DISPATCH entry + one handler function. Nothing else changes.
- Workers are stateless and identical — automatic load balancing.
- Business logic (data/file/output handlers) is pure and unit-testable with no process tree.
- Archive support (Phase 5 — ultimately zip, 7z, and tar) is the acceptance test for extensibility — zero changes to coordinator or worker, proven three times over.

---

## Implementation Phases

| Phase | Theme | Key Deliverable |
|---|---|---|
| **0** | Standards & Scaffolding | snake_case rename; ruff `N`; `run_scan()` extracted; module stubs |
| **1** | Core Infrastructure | `Task`/`TaskResult`, `WorkerContext`, `worker_loop`, `QueueListener`; NOOP integration test on Windows |
| **2** | Coordinator & Control Flow | Fan-out loop, `pending` termination, deadline detection, `rich.Live` progress |
| **3** | Business Logic Re-contracted | `protocols.py`, `FilesystemItem`, real handlers, `Config` model, output sinks, CLI |
| **4** | Hardening & Parity | Heartbeat restart, base64-xml < 5 min, baseline comparison, old code deleted, ≥ 80% coverage |
| **5** | Archive Support | ZIP first, then generalized to a format registry (7z, tar) — archive task types, safety limits, zero changes to coordinator/worker |

---

## Code Quality Requirements

| Requirement | Enforcement |
|---|---|
| `snake_case` / `PascalCase` / `UPPER_CASE` everywhere | ruff `N` ruleset in CI |
| 100% type hint coverage | mypy `--strict` in CI |
| Pydantic v2 for all data models | Code review; `dataclass` only for `WorkerContext` (documented exception) |
| Test coverage ≥ 80% | pytest-cov gate in CI |
| Zero ruff violations | ruff CI gate |

---

## Success Criteria (merge to `main`) — all met

- [x] Entire orchestration layer replaced; old process code deleted — `classes.py`, `piidigger.py`, `queuefuncs.py`, `filescan.py`, `globalvars.py` no longer exist in `src/piidigger/`; enforced in CI by `test_phase4.py::test_no_legacy_orchestration_references`
- [x] All identifiers PEP 8 compliant; ruff + mypy clean — verified 2026-07-06 (`ruff check src/ tests/`, `mypy src/` both clean)
- [x] Business logic unit-testable without a process tree — handler unit tests call handler functions directly, no `mp.Process` involved
- [x] `base64-xml-test.xml` completes < 5 minutes; timeout logged — resolved at the root cause (regex catastrophic backtracking) via an `@`-prefilter; see `test_email.py::test_email_prefilter_skips_regex_on_long_no_at_string`
- [x] Graceful `Ctrl+C` — no orphan processes, no temp files — verified on POSIX (`test_coordinator.py::test_ctrl_c_exits_within_5_seconds`); the Windows-specific automated test is skipped due to a test-harness limitation (`mp.Process` doesn't expose cross-process SIGINT delivery), not a difference in `run_coordinator()`'s `KeyboardInterrupt` handling itself
- [x] 2.0 output baseline set with lineage fields; baseline comparison passes — one-time migration validation: v2 is a strict superset of v1 (0 regressions, 4 improvements); see `IMPLEMENTATION_CHECKLIST.md` for the documented rationale on why no permanent baseline test was kept
- [x] Archive support (Phase 5 — zip, later extended to 7z and tar) adds zero changes to `coordinator.py` or `worker.py`
- [x] Test coverage ≥ 80% — 84% overall, verified 2026-07-06
