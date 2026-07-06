# Installation and Getting Started Guide

## Overview

PIIDigger is a Python-based application and distributed through PyPI. If you're already familiar with Python, you can use any of the methods you already know (pip, pipx, uv, etc) to install it.

If you're not already familiar with managing Python-based applications, then I would recommend using Astral's `uv` Python package manager utility.  It will manage the Python version and all of the third party dependencies.  It will also allow you to easily update PIIDigger in the future.

## Prerequisites

### System Requirements
- Any Linux, MacOS, or Windows operating system capable of running Python
- Python 3.14
- Astral UV

### Knowledge Requirements
- Basic Powershell or Linux/MacOS shell navigation

### Required Files/Data
- None

## Getting Started

**NOTE:** If UV is not desirable for any number of reasons, there are standalone Windows and Linux packages available on the releases page (https://github.com/kirkpatrickprice/PIIDigger/releases).

### First Steps -- UV Installation

1. Install Astral's UV from [UV documentation](https://docs.astral.sh/uv/) if not already installed 
    **NOTE:** The UV documentation includes instructions for MacOS, Windows, and Linux.
2. Launch a terminal session
    **Windows** Windows Terminal (already installed on Windows 11)
    **MacOS/Linux** Open a terminal session
3. Run the following commands to install PIIDigger

    ```powershell
    # Install KPAT using UV
    uv tool install piidigger
    ```

This will download the installation packages from the official [Python Package Index](https://pypi.org/project/PIIDigger/).

**FUTURE NOTE:** In the future, if updating UV is necessary -- for instance to support later versions of Python -- you can use the following command:

```powershell
# Update the UV environment
uv self update
```

### Test the Installation

```powershell
# Test the installation
piidigger --version
```

You should see something similar to the following:
```powershell
PIIDigger version: 2.0.0
```

***Note:*** The first attempt to run the command may take a bit longer on Windows as Windows Defender performs an AV scan on the program files.

### Updating PIIDigger
When new versions of PIIDigger are released, you can update your local installation with the following command:

```powershell
# Update PIIDigger using UV
uv tool upgrade piidigger
```

See the note above about updating `uv` as well. Future versions of PIIDigger may also require updates to Python itself -- for instance, to fix any vulnerabilities or to take advantage of performation improvements.

### Basic Usage

#### Primary Command
The most basic command to use PIIDigger is:

```powershell
# Run a scan using default settings
piidigger
```

This will run a scan with default settings:

* Scan all available Windows drive letters or Linux/MacOS disk partitions
* Scan for all supported file types
* Scan inside all supported archive files
* Scan for all supported sensitive data types
* Use the "Balanced" profile (75% of physical cores)
* Produce output files in all supported versions under the `<current_folder>/piidigger-results/` folder
* Produce a log in `current_folder/logs` using the `INFO` log level setting 

#### Additional commands
PIIDigger includes additional subcommands including:

```powershell
# Scan command -- this is also the default if no sub-command is provided
piidigger scan

# Config command to create and validate configuration files
piidigger config

# Inspect command identy file encoding or MIME types and to list various supported data and file types
piidigger inspect
```

#### Accessing the help system
Append `--help` to the end of any `piidigger` command to see the available options:

```powershell
# Display help for scans
piidigger scan --help

# Display help for inspect
piidigger inspect --help
```

## Verifying Your Download and Troubleshooting the Install

### Verifying file integrity (standalone packages)

If you downloaded a standalone package from the [releases page](https://github.com/kirkpatrickprice/PIIDigger/releases) instead of using `uv`, confirm it matches the published hash before running it:

```powershell
# Confirm the downloaded Windows ZIP matches the hash listed on the release page
Get-FileHash .\piidigger-<architecture>.zip
```

```bash
# Confirm the downloaded Linux ZIP matches the hash listed on the release page
sha256sum -b linux.zip
```

### Windows Anti-Virus

PIIDigger's standalone Windows packages use Embedded Python distributed directly by the Python Software Foundation, which avoids most of the false-positive AV detections that affect PyInstaller-style Python packaging. Every release is also tested against VirusTotal before publishing. If your AV product still flags PIIDigger, please [open an issue](https://github.com/kirkpatrickprice/PIIDigger/issues) so it can be investigated.

### macOS permissions

On macOS 14 (Sonoma) and later, the OS prompts the first time an unrecognized application tries to access certain user data folders (Documents, Desktop, Downloads, etc.). Grant PIIDigger access through these prompts — scans of those folders will otherwise silently skip files it isn't permitted to read.

## Related Documentation

- [⚠️ Breaking Changes](breaking-changes.md) — read this before upgrading from 1.x
- [Scan Command Guide](scan-command.md)
- [Config Command Guide](config-command.md)
- [Inspect Command Guide](inspect-command.md)
- [Advanced Configuration](advanced-configuration.md)
- [Troubleshooting](troubleshooting.md)

---