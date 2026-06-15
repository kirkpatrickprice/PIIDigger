# Architecture Refactor Documentation

This directory contains comprehensive documentation for the PIIDigger architecture refactor.

**Branch**: `refactor`  
**Status**: Design & Planning Phase  
**Timeline**: 3-4 weeks estimated

---

## Quick Navigation

### For Project Overview
→ Start here: **[ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md)**
- Executive summary of what's changing and why
- Current architecture analysis with diagrams
- Proposed task queue + worker pool solution
- Implementation phases and timeline

### For Understanding Current Problems
→ **[CURRENT_ISSUES.md](./CURRENT_ISSUES.md)**
- Detailed analysis of each architectural issue
- Specific file references with code examples
- Impact on current system (timeouts, extensibility, load balancing)
- What the refactor fixes

### For Implementation Guidance
→ **[IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md)**
- Step-by-step tasks organized by phase
- Checkboxes for progress tracking
- Open questions and known risks
- Phase-wise breakdown (5 phases total)

### For Testing Plan
→ **[TESTING_STRATEGY.md](./TESTING_STRATEGY.md)**
- Multi-layered testing approach (unit, integration, E2E)
- Actual test code examples for reference
- Fixture setup and test organization
- Coverage requirements (≥80%)
- Known test challenges and solutions

---

## Architecture at a Glance

**Current Problem**: Tightly-coupled SENTINEL-based process orchestration breaks when extended, causes hangs, poor load balancing.

**Proposed Solution**: 
```
Task Queue (holds polymorphic task objects)
    ↓
[Worker Pool - generic workers, all the same]
    ↓
Result Queue (collects TaskResults)
    ↓
[Output Processors]
```

**Key Benefits**:
- ✓ Adding new task type = 1 elif branch
- ✓ Load balancing automatic (workers pull any task)
- ✓ Timeout enforcement reliable (process-level, separate GIL)
- ✓ Fault tolerant (hung worker doesn't block others)
- ✓ Clean, testable code (Pythonic, typed, documented)

---

## Implementation Phases

| Phase | Focus | Duration | Key Deliverable |
|-------|-------|----------|-----------------|
| **1** | Core Infrastructure | 2-3 days | Task model, worker pool, timeout executor |
| **2** | Task Handlers | 3-4 days | Polymorphic handlers for all work types |
| **3** | Orchestrator | 2-3 days | Task coordinator, main loop, shutdown |
| **4** | Code Quality | 2-3 days | Ruff compliance, type hints, tests |
| **5** | Validation | 1-2 days | Baseline comparison, edge cases |

---

## Code Quality Requirements

- **Naming**: Real names with underbars (PEP8) - NOT `dirsQ`, YES `dirs_queue`
- **Type Hints**: 100% coverage - all functions, parameters, return types
- **Documentation**: Comprehensive docstrings (Args, Returns, Examples)
- **Linting**: 0 Ruff violations - enforced via CI
- **Testing**: ≥80% coverage with unit/integration/E2E tests

---

## Success Criteria

- [ ] All tests pass (unit, integration, E2E)
- [ ] Ruff linting: 0 violations
- [ ] Type coverage: 100%
- [ ] Test coverage: ≥80%
- [ ] base64-xml-test.xml completes in <5 minutes (previously hung 2-4min)
- [ ] Output formats match baseline exactly
- [ ] No SENTINEL-based timeouts in logs
- [ ] Graceful shutdown on Ctrl+C

---

## Getting Started (When You Resume)

1. **Review Documents** (30 mins)
   - Read ARCHITECTURE_REDESIGN.md for overview
   - Skim CURRENT_ISSUES.md for problem context
   
2. **Set Up Testing Framework** (1 day)
   - Create test fixtures and conftest.py
   - Implement unit test skeleton

3. **Phase 1** (2-3 days)
   - Create `src/piidigger/orchestration/` module
   - Implement Task, TaskResult, WorkerPool classes
   - Write unit tests for each

4. **Phase 2** (3-4 days)
   - Implement handler functions (migrate existing logic)
   - Write unit tests for handlers
   - Integration test: handler → queue flow

5. **Phase 3** (2-3 days)
   - Implement TaskCoordinator
   - Refactor main() to use new orchestration
   - Integration tests: full pipeline

6. **Phase 4** (2-3 days)
   - Run Ruff linting, fix all violations
   - Add type hints everywhere
   - Expand test coverage

7. **Phase 5** (1-2 days)
   - Run baseline comparison
   - Edge case validation
   - Final testing and sign-off

---

## Key Design Decisions

### Why Task Queue Instead of Specialized Processes?

**Task Queue Model**:
- Single source of truth for work (Task objects)
- Generic workers (no role specialization)
- Automatic load balancing

**vs. Current SENTINEL Model**:
- Rigid role assignment (findDirs, findFiles, fileHandlers)
- Manual coordination between roles
- No load balancing

**Impact**: Can add parallelization options (e.g., parallel file enumeration) by simply defining new task types. Current system would require rewriting SENTINEL choreography.

### Why Pydantic v2 Models?

- **IPC Validation**: Tasks deserialized from multiprocessing queues are automatically validated
- **Type Safety**: Enum validation for task_type, range validation for timeout_seconds
- **Zero Dependencies**: Pydantic v2 has no transitive dependencies (optimized in Rust)
- **Production Standard**: Battle-tested across Python ecosystem (Celery, FastAPI, etc.)
- **Serialization**: Native pickle + JSON support for multiprocessing queues
- **Error Messages**: Rich validation errors with clear feedback on what failed

### Why Separate Coordinator Process?

- Orchestration logic decoupled from work execution
- Can monitor task results, adjust task generation
- Easier to debug (single process driving flow)
- Existing pattern in production systems (Celery, RQ, etc.)

---

## Questions Before Starting?

If resuming this work, consider:

1. **Any new insights** from the current codebase investigation?
2. **Team preference** on async vs. process-based?
3. **Output format changes** acceptable (currently frozen)?
4. **Deprecation timeline** for old ProcessManager class?

---

## Reference Links

- Python multiprocessing docs: https://docs.python.org/3/library/multiprocessing.html
- Ruff formatter: https://docs.astral.sh/ruff/
- Pytest fixture guide: https://docs.pytest.org/en/latest/fixture.html
- PEP8 style guide: https://www.python.org/dev/peps/pep-0008/

---

## Document Index

| Document | Purpose | Audience |
|----------|---------|----------|
| [ARCHITECTURE_REDESIGN.md](./ARCHITECTURE_REDESIGN.md) | Full design proposal | Technical leads, architects |
| [CURRENT_ISSUES.md](./CURRENT_ISSUES.md) | Problem catalog | Developers, code reviewers |
| [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md) | Task tracking | Developers (during implementation) |
| [TESTING_STRATEGY.md](./TESTING_STRATEGY.md) | Test plan | QA, developers |
| **README.md** (this file) | Navigation & overview | Everyone |

---

**Last Updated**: 2026-03-06  
**Branch**: `refactor`  
**Status**: Ready for Implementation
