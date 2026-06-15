# PIIDigger

PIIDigger scans the file system for **Personally Identifiable Information** (PII)
such as credit-card numbers (PANs) and email addresses across common file types
(plain text, PDF, Word, Excel).

This documentation site is generated with [MkDocs](https://www.mkdocs.org/) and
the [Material](https://squidfunk.github.io/mkdocs-material/) theme.

## Where to start

- **[Architecture Refactor](refactor/README.md)** — the in-progress redesign that
  replaces the rigid SENTINEL-based pipeline with a polymorphic task queue, and the
  rationale behind it.
- **[API Reference](reference/api.md)** — API docs generated from source docstrings.

## Project links

- Source: <https://github.com/kirkpatrickprice/PIIDigger>
- Issues: <https://github.com/kirkpatrickprice/PIIDigger/issues>

!!! note
    The repository `README.md`, `PERFORMANCE.md`, and `ERRATA.md` remain the
    canonical user-facing guides. As the refactor proceeds, that content will be
    migrated into this site.
