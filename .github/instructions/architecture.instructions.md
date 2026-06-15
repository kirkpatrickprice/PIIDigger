# Architecture Documentation Standards for PIIDigger

**Purpose**: Ensure consistent, comprehensive, and maintainable architecture documentation across all PIIDigger services and components.

**Template**: Always use `docs/templates/architecture-document-template.md` as the starting point. Replace all `{placeholder}` text.

---

## Document Structure (Required Sections)

1. **Overview** — Purpose, Context, Status, Scope
2. **Architectural Principles** — 5 Design Goals + 5 Key Benefits (bold names with descriptions)
3. **Architecture Overview Diagram** — Mermaid diagram (see Mermaid Standards below)
4. **Protocols** — If applicable: complete protocol definitions with type hints
5. **Configuration Models** — If applicable: Pydantic models with validators + YAML examples
6. **Service Implementation** — Main class, dependencies, key methods
7. **Container Integration** — DI setup, provider types, service aggregates
8. **CLI Integration** — Command implementations, Click decorators, registration
9. **Service Integration Patterns** — Cross-service usage, common patterns
10. **Usage Examples** — CLI examples, code examples, multiple scenarios
11. **Performance Considerations** — 3 distinct performance areas with concrete strategies
12. **Testing Patterns** — Mock services, integration tests, fixtures, coverage
13. **Implementation Roadmap** — 4 logical phases with checkboxes and dependencies

---

## Mermaid Diagram Standards

**Required Elements**:
- Comprehensive, showing all major components and relationships
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
- **Examples**: Complete, runnable, syntactically correct
- **Testing**: Test snippets before including; use realistic parameter names
- **Error handling**: Include proper exception handling in examples
- **Docstrings**: Comprehensive docstrings for all code examples

---

## File Naming & Location

- **Path**: `docs/architecture/{domain}/{service-name}.md`
- **Naming**: kebab-case (e.g., `cache-service-interface.md`)
- **Links**: Use relative paths; link to related docs and implementation files

---

## Visual Elements

- **Emoji**: Use sparingly in diagram subgraph titles only (📁, 🔌, 💾, etc.)
- **Styling**: Consistent Mermaid CSS classes across all diagrams
- **Accessibility**: Meaningful alt text for visual elements
- **Tables**: Use for structured comparisons

---

## Pre-Submission Review Checklist

- [ ] All `{placeholders}` replaced with real content
- [ ] Mermaid diagram renders correctly and is visually clear
- [ ] All code examples are syntactically correct
- [ ] Protocol definitions have comprehensive type hints
- [ ] Configuration examples are realistic and complete
- [ ] CLI examples use correct command structure
- [ ] Performance strategies are specific and actionable
- [ ] Testing patterns are complete and demonstrate best practices
- [ ] Implementation roadmap is logical and achievable
- [ ] Document follows these standards
- [ ] All cross-references link correctly
- [ ] File is in correct directory with kebab-case naming

---

## Maintenance

- **Quarterly review** for accuracy
- **Update** implementation roadmaps as features complete
- **Refresh** code examples when APIs change
- **Archive** outdated documents in `docs/archive/` with deprecation notes
