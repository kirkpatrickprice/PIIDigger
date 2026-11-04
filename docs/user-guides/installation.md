# Installation and Getting Started Guide

## Overview

The recommended way to install PIIDigger is to download a standalone package from [GitHub Releases](https://github.com/kirkpatrickprice/PIIDigger/releases). Each package bundles its own Python runtime, so there is nothing else to install. Packages are published for:

| Platform | Release file |
|---|---|
| Windows (x64) | `piidigger-<version>-<commit>-windows-amd64.zip` |
| Linux (x86_64) | `piidigger-<version>-<commit>-linux-x86_64-manylinux2014.tar.gz` |
| macOS (Apple Silicon) | `piidigger-<version>-<commit>-macos-arm64.tar.gz` |
| macOS (Intel) | `piidigger-<version>-<commit>-macos-x86_64.tar.gz` |

PIIDigger is also published to the [Python Package Index](https://pypi.org/project/PIIDigger/). If a standalone package doesn't suit your environment, see [Alternative: Install with uv](#alternative-install-with-uv).

## Prerequisites

### System Requirements
- Windows, Linux (glibc 2.17 or later), or macOS
- Internet access to `github.com` and `api.github.com`

### Knowledge Requirements
- Basic PowerShell or Linux/macOS shell navigation

### Required Files/Data
- None

## Getting Started

### Download the Latest Release

Open a terminal in the folder where you want PIIDigger to live, then paste the block for your platform. Each block asks GitHub for the most recent release, downloads the package for your platform along with `checksums.txt`, and unpacks it.

#### Windows (PowerShell)

```powershell
$asset = (Invoke-RestMethod https://api.github.com/repos/kirkpatrickprice/PIIDigger/releases/latest).assets | Where-Object name -like '*-windows-amd64.zip'
Invoke-WebRequest $asset.browser_download_url -OutFile $asset.name
Invoke-WebRequest ($asset.browser_download_url -replace '[^/]+$', 'checksums.txt') -OutFile checksums.txt
Expand-Archive $asset.name -DestinationPath . -Force
```

PIIDigger is unpacked into the `piidigger-amd64` folder. Run it with `.\piidigger-amd64\piidigger.cmd`.

#### Linux (bash)

```bash
url=$(wget -qO- https://api.github.com/repos/kirkpatrickprice/PIIDigger/releases/latest | grep -o 'https://[^"]*-linux-x86_64-manylinux2014\.tar\.gz')
tarball=${url##*/}
wget "$url" "${url%/*}/checksums.txt"
tar -xzf "$tarball"
```

PIIDigger is unpacked into the `piidigger` folder. Run it with `./piidigger/piidigger`.

#### macOS (zsh/bash)

macOS doesn't include `wget`, so this block uses `curl`. It picks the Apple Silicon (`arm64`) or Intel (`x86_64`) package to match your Mac.

```bash
arch=$(uname -m)
url=$(curl -fsSL https://api.github.com/repos/kirkpatrickprice/PIIDigger/releases/latest | grep -o "https://[^\"]*-macos-$arch\.tar\.gz")
tarball=${url##*/}
curl -fL -O "$url" -O "${url%/*}/checksums.txt"
tar -xzf "$tarball"
```

PIIDigger is unpacked into the `piidigger` folder. Run it with `./piidigger/piidigger`. See [macOS Gatekeeper](#macos-gatekeeper-unsigned-standalone-builds) if macOS refuses to run it.

!!! tip "Running the examples in this guide"
    The examples in the rest of this guide call `piidigger` directly. To do the same, add the unpacked folder (`piidigger-amd64` on Windows, `piidigger` on Linux/macOS) to your `PATH`, or type the full path to the program shown above.

### Verify the Download

Run this in the same terminal session and folder as the download block. It compares the package against its entry in `checksums.txt`.

```powershell
# Windows -- prints True when the file matches
(Get-FileHash $asset.name).Hash -eq (Select-String -Path checksums.txt -SimpleMatch $asset.name).Line.Split()[0]
```

```bash
# Linux -- prints "<file>: OK" when the file matches
grep "$tarball" checksums.txt | sha256sum -c
```

```bash
# macOS -- prints "<file>: OK" when the file matches
grep "$tarball" checksums.txt | shasum -a 256 -c
```

Don't run PIIDigger if the check fails. Download it again, and [open an issue](https://github.com/kirkpatrickprice/PIIDigger/issues) if it still fails.

### Test the Installation

```powershell
# Test the installation
piidigger --version
```

You should see something similar to the following:
```powershell
PIIDigger version: 2.0.0 (1a2b3c4)
```

The value in parentheses is the short ID of the commit the release was built from. Include the full line when you report an issue.

***Note:*** The first attempt to run the command may take a bit longer on Windows as Windows Defender performs an AV scan on the program files.

### Updating PIIDigger

To update a standalone install, delete the unpacked folder and run the [download block](#download-the-latest-release) again.

Before deleting it, check whether it holds anything you want to keep. By default, PIIDigger writes its `piidigger-results/` and `logs/` folders into the folder you ran it from. If you ran it from inside the unpacked folder, or saved configuration files there, move them somewhere else first.

### Alternative: Install with uv

Use this method if a standalone package isn't available for your platform, or if you already manage Python tools with `uv`. Astral's [`uv`](https://docs.astral.sh/uv/) installs PIIDigger from PyPI and manages the Python version and all third-party dependencies for you. If you already use pip or pipx, those work too.

1. Install `uv` by following the [uv documentation](https://docs.astral.sh/uv/getting-started/installation/), which covers Windows, macOS, and Linux.
2. Open a terminal session.
    - **Windows:** Windows Terminal (already installed on Windows 11)
    - **macOS/Linux:** any terminal
3. Install PIIDigger:

    ```powershell
    # Install PIIDigger using uv
    uv tool install piidigger
    ```

To update a `uv` install later:

```powershell
# Update PIIDigger using uv
uv tool upgrade piidigger
```

Future versions of PIIDigger may need a newer Python. If so, update `uv` itself first:

```powershell
# Update uv
uv self update
```

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

## Troubleshooting the Install

### Windows Anti-Virus

PIIDigger's standalone Windows packages use Embedded Python distributed directly by the Python Software Foundation, which avoids most of the false-positive AV detections that affect PyInstaller-style Python packaging. Every release's standalone binaries (Windows, Linux, and macOS) are automatically scanned with VirusTotal as part of the release pipeline, and a link to each file's analysis is posted directly in that release's notes on the [releases page](https://github.com/kirkpatrickprice/PIIDigger/releases).

If your AV product still flags the standalone package, [install with uv](#alternative-install-with-uv) instead. That method installs PIIDigger from PyPI and runs it on a standard Python interpreter instead of the bundled one. You're welcome to [open an issue](https://github.com/kirkpatrickprice/PIIDigger/issues) about the detection, but false positives in individual AV products may not get a fix.

### macOS permissions

On macOS 14 (Sonoma) and later, the OS prompts the first time an unrecognized application tries to access certain user data folders (Documents, Desktop, Downloads, etc.). Grant PIIDigger access through these prompts — scans of those folders will otherwise silently skip files it isn't permitted to read.

### macOS Gatekeeper (unsigned standalone builds)

The macOS standalone builds are not code-signed or notarized — that requires a paid Apple Developer account and a notarization pipeline, which isn't set up for this project. If you downloaded the package with a browser, Gatekeeper will refuse to run the binary the first time and label it from an "unidentified developer." To run it anyway, either right-click the binary and choose **Open** (then confirm in the dialog), or clear the quarantine attribute yourself:

```bash
xattr -dr com.apple.quarantine piidigger
```

If this friction is a blocker for your use case, try [installing with uv](#alternative-install-with-uv) instead. Gatekeeper normally doesn't check packages installed that way.

## Related Documentation

- [⚠️ Breaking Changes](breaking-changes.md) — read this before upgrading from 1.x
- [Scan Command Guide](scan-command.md)
- [Config Command Guide](config-command.md)
- [Inspect Command Guide](inspect-command.md)
- [Advanced Configuration](advanced-configuration.md)
- [Troubleshooting](troubleshooting.md)
