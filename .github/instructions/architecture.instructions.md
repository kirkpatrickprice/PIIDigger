# Architecture Documentation Standards for PIIDigger

**Purpose**: Keep architecture docs useful for a single-process CLI tool with no public-library surface. Document the coordinator/worker orchestration model and its extension points — not a service-oriented or DI-based system this project doesn't have.

**Template**: Always use `docs/templates/architecture-document-template.md` as the starting point. Replace all `{placeholder}` text. Delete any optional section that doesn't apply rather than leaving it filled with invented content.

**Scope check before writing a new doc**: does this component have its own extension contract (a `Protocol`) or a non-obvious data flow? If it's a single class with no protocol and no fan-out behavior, a docstring or a paragraph in an existing doc is enough — it doesn't need its own architecture document.

---

## Document Structure

Required:
1. **Overview** — Purpose, Context, Status, Scope
2. **Architectural Principles** — 3-5 Design Goals + 3-5 Key Benefits (bold names with descriptions)
3. **Architecture Diagram** — Mermaid diagram (see Mermaid Standards below)
4. **Core Implementation** — main class/function(s), how they're constructed (plain constructor calls — this project has no DI container), key methods

Include only if applicable — delete the heading if not:
5. **Protocols** — protocol definitions this component implements or defines, with type hints
6. **Configuration Models** — Pydantic models with validators + YAML example
7. **Extension Points** — how a contributor adds a new handler / task type / sink; what the `DISPATCH` or registration entry looks like
8. **Usage Examples** — one CLI example, one programmatic example
9. **Performance Considerations** — only for components with a real perf story (multiprocessing, streaming, large files)
10. **Testing Notes** — non-obvious test seams or fixture requirements not already covered by `docs/architecture/quality/testing-requirements.md`
11. **Cross-References** — links to related docs, `ARCHITECTURE_REDESIGN.md`, or implementation files

Omit entirely (do not carry these into new docs): Container Integration, CLI framework wiring (Click decorator boilerplate), Service Integration Patterns as a standalone section, Implementation Roadmap — roadmap and phase tracking belong in `docs/refactor/IMPLEMENTATION_CHECKLIST.md`, not in per-component architecture docs.

---

## Mermaid Diagram Standards

**Required Elements**:
- Show the components and relationships that matter for understanding this piece — not every node in the system
- Subgraphs with emoji icons for logical boundaries
- Descriptive node labels with brief descriptions
- Data flow and dependencies clearly shown

**CSS Styling Classes** (apply consistently):
- `coreService` — Core infrastructure services
- `protocol` — Protocol interfaces
- `component` — Service components
- `integration` — Integration points
- `cli` — CLI interfaces
- `storage` — Storage/persistence
- `group` — Subgraph styling

**Example subgraph with emoji**:
```
subgraph core["🔧 Core Services"]
    direction LR
    service["ServiceName"]:::coreService
end
```

---

## Writing Style

- **Tense**: Present tense for current; "will" for future/planned
- **Avoid**: "ensure", "comprehensive", "strict", "rigorous", "well-defined", "effective"
- **Sentence length**: Keep under 25 words
- **Voice**: Active voice preferred
- **Clarity**: Direct, specific language

---

## Code Standards

- **Python version**: 3.14+ with modern type hints
- **Type hints**: Use `|` (union) and `X | None` (not `Optional[X]` or `typing.Union`)
- **Imports**: Absolute imports only; no `from typing import *`
- **Examples**: Complete, syntactically correct, and reflect real constructs from this codebase (`Protocol`, Pydantic `BaseModel`, `dataclass(frozen=True)` for `WorkerContext`-style holders) — not a framework this project doesn't use
- **Error handling**: Include proper exception handling in examples

---

## File Naming & Location

- **Path**: `docs/architecture/{domain}/{component-name}.md`
- **Naming**: kebab-case (e.g., `worker-orchestration.md`)
- **Links**: Use relative paths; link to related docs and implementation files

---

## Pre-Submission Review Checklist

- [ ] All `{placeholders}` replaced with real content
- [ ] Optional sections that don't apply are deleted, not left as boilerplate
- [ ] Mermaid diagram renders correctly and reflects actual code structure
- [ ] Code examples are syntactically correct and match this codebase's actual patterns (no DI container, no framework this project doesn't use)
- [ ] Protocol definitions (if included) have complete type hints
- [ ] Configuration examples (if included) are realistic and complete
- [ ] Document follows these standards
- [ ] All cross-references link correctly
- [ ] File is in correct directory with kebab-case naming

---

## Maintenance

- **Update** when the described component's protocol or extension mechanism changes
- **Refresh** code examples when APIs change
- **Archive** outdated documents in `docs/archive/` with deprecation notes
