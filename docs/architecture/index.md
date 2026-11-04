# Architecture

These documents describe how PIIDigger 2.0 works today. They are written for contributors and maintainers. For how to use PIIDigger, see the [User Guides](../user-guides/installation.md).

| Document | What it covers | Read it when… |
|---|---|---|
| **[Coordinator/Worker Task Pipeline](orchestration/coordinator-worker-pipeline.md)** | How one coordinator feeds worker processes through a task queue, tracks outstanding work, and recovers from stuck, crashed or oversized workers | You're changing anything in `orchestration/`, or adding a task type |
| **[Archive Handling](archives/archive-handling.md)** | The `ArchiveHandler` protocol, the format registry, and how an archive is split into scan batches | You're adding an archive format or changing how members are scanned |
| **[PDF Memory Limits](filehandlers/pdf-memory-limits.md)** | How the PDF handler sets pypdf's parsing limits from the worker memory limit, and the measurements behind them | You're changing the PDF handler or `max_worker_memory_mb` |
| **[Testing Requirements](quality/testing-requirements.md)** | What every component's tests need: unit and integration requirements, fixtures, coverage and runtime budgets | You're writing a component or reviewing its tests |
| **[CI/CD Pipeline](ci-cd/pipeline.md)** | How GitHub Actions checks pull requests and publishes a release to PyPI, GitHub Releases and GitHub Pages | You're changing a workflow or cutting a release |

## Related Documentation

- [Extending PIIDigger](../reference/extending.md) — step-by-step guides for adding a file handler, data handler, output sink or archive format
- [Architecture Refactor](../refactor/README.md) — the design rationale and build record of the 2.0 rewrite, kept as a historical record
