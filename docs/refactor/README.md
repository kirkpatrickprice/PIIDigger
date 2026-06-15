# Architecture Refactor Documentation

**Branch**: `refactor`
**Status**: Design Locked — Phase 0 Ready to Start
**Last Updated**: 2026-06-15
**Target release**: 2.0.0

---

## Quick Navigation

| Document | Purpose | Start here when… |
|---|---|---|
| **[ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md)** | Complete design: goals, module layout, all 6 phases, open decisions | You need to understand what we're building and why |
| **[IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md)** | Actionable per-phase task list with exit criteria | You're about to write code |
| **[TESTING_STRATEGY.md](./TESTING_STRATEGY.md)** | Test structure, fixtures, examples, coverage targets | You're writing tests or reviewing test coverage |
| **[CURRENT_ISSUES.md](./CURRENT_ISSUES.md)** | Historical record of 1.x problems and their 2.0 dispositions | You want context on why a decision was made |
| **[ZIP_HANDLING_PLAN.md](./ZIP_HANDLING_PLAN.md)** | ZIP archive support: task types, safety limits, test fixtures | You're working on Phase 5 |

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
- ZIP archive support (Phase 5) is the acceptance test for extensibility — zero changes to coordinator or worker.

---

## Implementation Phases

| Phase | Theme | Key Deliverable |
|---|---|---|
| **0** | Standards & Scaffolding | snake_case rename; ruff `N`; `run_scan()` extracted; module stubs |
| **1** | Core Infrastructure | `Task`/`TaskResult`, `WorkerContext`, `worker_loop`, `QueueListener`; NOOP integration test on Windows |
| **2** | Coordinator & Control Flow | Fan-out loop, `pending` termination, deadline detection, `rich.Live` progress |
| **3** | Business Logic Re-contracted | `protocols.py`, `FilesystemItem`, real handlers, `Config` model, output sinks, CLI |
| **4** | Hardening & Parity | Heartbeat restart, base64-xml < 5 min, baseline comparison, old code deleted, ≥ 80% coverage |
| **5** | ZIP Support | `ArchiveMemberItem`, archive task types, safety limits — zero changes to coordinator/worker |

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

## Success Criteria (merge to `main`)

- [ ] Entire orchestration layer replaced; old process code deleted
- [ ] All identifiers PEP 8 compliant; ruff + mypy clean
- [ ] Business logic unit-testable without a process tree
- [ ] `base64-xml-test.xml` completes < 5 minutes; timeout logged
- [ ] Graceful `Ctrl+C` — no orphan processes, no temp files
- [ ] 2.0 output baseline set with lineage fields; baseline comparison passes
- [ ] ZIP (Phase 5) adds zero changes to `coordinator.py` or `worker.py`
- [ ] Test coverage ≥ 80%
