# PIIDigger

**PIIDigger** is a program to identify Personally Identifiable Information (PII) in common file types.

> **⚠️ Upgrading from 1.x?** Read [Breaking Changes](https://kirkpatrickprice.github.io/PIIDigger/user-guides/breaking-changes/) first — your existing `piidigger.toml` will not load under 2.0.

## Features

- Works anywhere Python 3.14+ is available; pre-built standalone packages also available for Windows and Linux
- Identifies files by extension and MIME type across plain text, PDF, Word (`.docx`), and Excel (`.xls`/`.xlsx`) documents
- Looks inside `.zip`, `.7z`, and `.tar` archives (and their compressed variants) without extracting them to disk ahead of time
- Extensible data handlers for identifying PII — currently payment card numbers and email addresses
- Fully configurable via a `piidigger.toml` file: scan scope, performance, output formats, and more
- Saves results in CSV, JSON, and plain-text formats

## Documentation

Full documentation — installation, running scans, the configuration reference, and troubleshooting — is published at **<https://kirkpatrickprice.github.io/PIIDigger>**.

## Installation

```powershell
uv tool install piidigger
```

See the [Installation Guide](https://kirkpatrickprice.github.io/PIIDigger/user-guides/installation/) for standalone-package alternatives and platform-specific notes.

## Usage

```powershell
piidigger
```

Runs a scan of the local system with default settings. See the [Scan Command Guide](https://kirkpatrickprice.github.io/PIIDigger/user-guides/scan-command/) for the full command reference, or run `piidigger --help`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — patches and features are welcome.
