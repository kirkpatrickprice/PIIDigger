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
- **[Architecture Refactor](refactor/README.md)** — the in-progress redesign that
  replaces the rigid SENTINEL-based pipeline with a polymorphic task queue, and the
  rationale behind it.
- **[Extending PIIDigger](reference/extending.md)** — how to add a new file handler, data handler, output sink, or archive format, for contributors extending the application.

## Project links

- Source: <https://github.com/kirkpatrickprice/PIIDigger>
- Issues: <https://github.com/kirkpatrickprice/PIIDigger/issues>
