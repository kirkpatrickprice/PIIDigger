# Config Command User Guide

## Overview

`piidigger config` is a command group with two subcommands — `generate` and `validate` — that manage the TOML configuration files consumed by `piidigger scan`. It doesn't run a scan itself; it's purely for creating and checking the files that control one.

This guide covers the `config` command's mechanics: what `generate` writes, what `validate` checks, and how the two fit into a normal workflow. For what every individual setting in the generated file actually does, see [Advanced Configuration](advanced-configuration.md), which has the full settings reference and common-scenario recipes.

## Prerequisites

### System Requirements
- PIIDigger installed (see [Installation Guide](installation.md))

### Knowledge Requirements
- Basic PowerShell or Linux/macOS shell navigation
- Basic familiarity with TOML syntax (`key = "value"`, `[section]` headers, `["list", "of", "values"]`)

### Required Files/Data
- None — `config generate` creates the file for you; there's nothing to bring beforehand.

## Getting Started

### First Steps

```powershell
piidigger config generate
```

This writes a fully-populated `piidigger.toml` in the current directory with every setting at its built-in default. Open it in any text editor and change only the settings you care about — everything else is safe to leave as-is.

## Core Functionality

### Basic Usage

#### `piidigger config generate [FILE]`

Writes a default configuration template to `FILE` (default: `piidigger.toml`).

```powershell
# Write to the default filename
piidigger config generate

# Write to a specific named file — useful for keeping multiple profiles
piidigger config generate finance-share.toml
```

`generate` refuses to overwrite a file that already exists, so it's safe to run without accidentally clobbering edits you've already made:

```
Error: 'piidigger.toml' already exists.  Delete it or choose a different path.
```

If you want a fresh copy, either delete or rename the existing file first, or generate under a new name.

#### `piidigger config validate [FILE]`

Reads `FILE` (default: `piidigger.toml`), checks it against PIIDigger's configuration schema, and reports whether it's usable.

```powershell
piidigger config validate piidigger.toml
```

A valid file reports:

```
'piidigger.toml': OK
```

An invalid file explains exactly what's wrong, including a suggestion when a setting name looks like a typo:

```
Error: invalid configuration in piidigger.toml:
- Unknown setting 'archives.enable'. Did you mean 'archives.enabled'?
```

`validate` runs the exact same checks that `scan -f` runs before it starts a scan — so a file that passes `validate` is guaranteed to at least get past `scan`'s own startup validation.

#### Working with Files

Both subcommands take an optional filename argument (`generate` and `validate` both default to `piidigger.toml`), so you can manage more than one named profile in the same folder:

```powershell
piidigger config generate finance-share.toml
piidigger config generate legal-share.toml
piidigger config validate finance-share.toml
```

## Configuration

### Default Settings

The template written by `generate` reflects `Config.default()` — every root-level setting, `[start_dirs]`/`[exclude_dirs]` (OS-keyed), `[results]`, and `[archives]` populated with PIIDigger's built-in defaults, in an order where root-level settings correctly appear above the first `[section]` header (see the TOML footgun note below).

### Custom Configuration

Edit the generated file directly, then re-run `validate` after every change. The full settings reference — every key, its default, and what it controls — lives in [Advanced Configuration](advanced-configuration.md#configuration-reference); this guide intentionally doesn't duplicate that table.

## Output and Results

`config generate` and `config validate` don't produce scan output — they only produce the config file itself (`generate`) and a pass/fail message (`validate`). `generate` exits non-zero if the target file already exists or can't be written (e.g. a read-only folder); `validate` exits non-zero if the file is invalid or missing.

### Interpreting Results

- `generate` success: `Default config written to <path>`.
- `validate` success: `'<path>': OK`.
- Either failing: an `Error: ...` message on stderr and a non-zero exit code — useful for scripting (`piidigger config validate piidigger.toml || exit 1` in a CI step, for example).

## Troubleshooting

### `generate` reports "already exists"

**Symptoms:** `Error: 'piidigger.toml' already exists.  Delete it or choose a different path.`

**Solutions:** `generate` never overwrites silently. Either delete/rename the existing file, or pass a different filename to `generate`.

### `validate` reports "Unknown setting"

**Symptoms:** `Error: invalid configuration in piidigger.toml: - Unknown setting '...'`

**Solutions:** The setting name doesn't exist in the current schema. If PIIDigger can guess what you meant, it says so (`Did you mean '...'?`). Otherwise, generate a fresh file to a new path and diff it against yours — a renamed or removed setting from an older PIIDigger version is the usual cause.

### `validate` reports "Unexpected keys found inside [section]"

**Symptoms:** A setting you intended as top-level (like `log_level`) is being reported as if it belongs to a `[section]`.

**Solutions:** In TOML, any `key = value` line written *after* a `[section]` header is parsed as part of that section, even if it wasn't meant to be. Move the flat setting above the first `[start_dirs]`/`[results]`/`[archives]` header in the file, or regenerate the template and re-apply your edits into the correctly-ordered copy.

### `validate` reports a TOML parse error about backslashes

**Symptoms:** `Error: invalid TOML in piidigger.toml: Unescaped '\' in a string`

**Solutions:** A Windows path was written with a single backslash (e.g. `C:\Windows`), which TOML doesn't allow unescaped inside a string. Use a forward slash (`C:/Windows`) or a doubled backslash (`C:\\Windows`) instead, then re-run `validate`.

### Getting Help

```powershell
piidigger config --help
piidigger config generate --help
piidigger config validate --help
```

## Examples and Use Cases

### Example 1: Standard first-time setup

**Use Case:** You've just installed PIIDigger and want a config file to start customizing.

```powershell
piidigger config generate
# edit piidigger.toml
piidigger config validate
# automatically picks up the piidigger.toml file
piidigger scan
```

### Example 2: Maintaining multiple named profiles

**Use Case:** You regularly scan two different shares with different settings (e.g. different `data_handlers` or `start_dirs`).

```powershell
piidigger config generate finance-share.toml
piidigger config generate legal-share.toml
# edit each independently
piidigger config validate finance-share.toml
piidigger config validate legal-share.toml
# scan with each config file
piidigger scan -f finance-share.toml
piidigger scan -f legal-share.toml
```

### Example 3: Recovering from a bad hand-edit

**Use Case:** `validate` is reporting an error you can't immediately place, and it's faster to start clean than track down the exact line.

```powershell
piidigger config generate piidigger.new.toml
# copy your customized values into piidigger.new.toml, in the same order
# as the fresh template, then:
piidigger config validate piidigger.new.toml
```

## Best Practices

### Workflow Recommendations

- Always run `validate` immediately after hand-editing a config file — it's cheap, and it catches the same errors that would otherwise only surface when `scan` refuses to start.
- Keep root-level settings above the first `[section]` header, matching the order `generate` produces. It's the single most common source of a confusing validation error.
- Give profile files descriptive names (`finance-share.toml`, not `config2.toml`) — `generate` and `validate` both take a filename argument specifically so you can maintain more than one.

### Performance Considerations

Neither subcommand touches the filesystem being scanned or spawns worker processes — both are effectively instantaneous, so there's no reason not to run `validate` as often as you like while iterating on a config file.

## Related Documentation

- [⚠️ Breaking Changes](breaking-changes.md) — the `piidigger.toml` format changed in 2.0; 1.x config files will not load
- [Scan Command Guide](scan-command.md)
- [Advanced Configuration](advanced-configuration.md) — full settings reference and common scenarios
- [Inspect Command Guide](inspect-command.md) — discover valid values for `data_handlers`, `include_exts`, and `archives.formats`
- [Installation Guide](installation.md)

---

**Next Steps:** Use the [Inspect Command Guide](inspect-command.md) to look up valid handler/extension/archive-type names before filling them into your config, then run a scan with the [Scan Command Guide](scan-command.md).
