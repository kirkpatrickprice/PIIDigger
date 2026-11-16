# PIIDigger

PIIDigger scans the file system for **Personally Identifiable Information** (PII)
such as credit-card numbers (PANs) and email addresses across common file types
(plain text, PDF, Word, Excel).

This documentation site is generated with [MkDocs](https://www.mkdocs.org/) and
the [Material](https://squidfunk.github.io/mkdocs-material/) theme.

## Where to start

- **[⚠️ Breaking Changes](user-guides/breaking-changes.md)** — upgrading from 1.x?
  Read this first — your existing `piidigger.toml` will not load.
- **[User Guides](user-guides/installation.md)** — installation, running scans,
  configuration, and troubleshooting.
- **[Architecture](architecture/index.md)** — how PIIDigger works today: the
  coordinator/worker pipeline, archive handling, PDF memory limits, testing
  requirements, and the CI/CD pipeline.
- **[Extending PIIDigger](reference/extending.md)** — how to add a new file handler,
  data handler, output sink, or archive format, for contributors extending the
  application.
- **[Architecture Refactor](refactor/README.md)** — the completed 2.0 redesign that
  replaced the rigid SENTINEL-based pipeline with a polymorphic task queue. Kept as a
  historical record of the rationale and build process; see Architecture for current
  behavior.

## Project links

- Source: <https://github.com/kirkpatrickprice/PIIDigger>
- Issues: <https://github.com/kirkpatrickprice/PIIDigger/issues>
