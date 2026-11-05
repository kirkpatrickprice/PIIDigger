# Contributing to PIIDigger
YES!!!  Please do!  On a good day, I'm a Python Hack.  So any help anyone wants to throw at it is very much appreciated.  Don't worry about CI tests.  Here's the criteria for merging:
1. Your patch works 
2. Your patch doesn't break stuff on the three supported platforms (Windows, Linux and MacOS)
3. Your patch builds well in Pyinstaller

That's it.

This is an initial release and -- sadly -- I still have a day job.  There's plenty to do:
* Refactor the file content extraction.
* Refactor results output handling
* Ongoing:
    * Add file handler types -- anything that Python can read is open game.
    * Add data handler types -- and the associated Regex tuning.

## Changing GitHub workflows

Run these three checks from the repo root before you commit any change under `.github/`.
The `Workflow Lint` CI workflow runs the same checks on every PR that touches `.github/`.

```bash
actionlint                     # syntax, expressions, job wiring, shellcheck on run: blocks
zizmor .github/workflows/      # security findings
pinact run --check             # every action is pinned to a commit SHA
```

- **Pin a new action:** after you add a `uses:` line, run `pinact run`. It rewrites the ref to `owner/action@<sha> # vX.Y.Z`.
- **Suppress a finding:** fix it if you can. Otherwise add `# zizmor: ignore[<rule>]` on the line, with a comment saying why.
- **Install:** get [actionlint](https://github.com/rhysd/actionlint) and [pinact](https://github.com/suzuki-shunsuke/pinact) from their release pages, and zizmor with `uv tool install zizmor`.

## Building a local dev release

A local build produces the same standalone bundle as a release, named
`piidigger-<version>-dev-<sha>-<platform>`. For example:
`dist/piidigger-2.0.0-dev-c11dff2-windows-amd64.zip`.

Every platform follows the same steps:

1. Read the version from `src/piidigger/__init__.py` and the commit from `git rev-parse --short=7 HEAD`.
2. Clean `dist/`. Earlier build archives (`*.zip`, `*.tar.gz`) stay; everything else goes.
3. Run `.github/scripts/write_build_info.py`. It records the commit, so `piidigger --version` prints `2.0.0 (c11dff2)`.
4. Install from `uv.lock` into a separate `.venv-build`, without dev dependencies.
5. Build and package the bundle.
6. Run `.github/scripts/smoke_test.py` against the bundle. It checks `--version` and scans sample files for card numbers.

Run each block from the repository root. Each one runs in its own scope and stops at the first
failing command, so a failed step never leaves you with a mislabeled build. The commit names `HEAD`
only. If `git status` shows uncommitted changes, the build includes them but its name doesn't say so.

### Windows (PowerShell, embedded Python)

The bundle uses the embeddable Python package from python.org, so the venv must use the exact same
patch version. The block finds the newest patch of the `.python-version` series that has an
embeddable package.

```powershell
& {
# Cmdlets stop via -ErrorAction Stop; native commands (git, uv) by exit code.  A global
# $ErrorActionPreference = "Stop" would make Windows PowerShell 5.1 abort on uv's stderr warnings.
function Assert-ExitCode([string]$step) { if ($LASTEXITCODE -ne 0) { throw "$step failed (exit $LASTEXITCODE)" } }
# A missing command leaves $LASTEXITCODE untouched, so check for them up front
Get-Command git, uv -ErrorAction Stop | Out-Null

$version = (Select-String -ErrorAction Stop -Path src\piidigger\__init__.py -Pattern '^__version__ = "(.+)"').Matches[0].Groups[1].Value
$sha = git rev-parse --short=7 HEAD; Assert-ExitCode "git rev-parse"
$series = (Get-Content -ErrorAction Stop .python-version).Trim()
$pyVersion = $null
foreach ($patch in 30..0) {
    $v = "$series.$patch"
    try {
        Invoke-WebRequest -ErrorAction Stop -Method Head -UseBasicParsing -Uri "https://www.python.org/ftp/python/$v/python-$v-embed-amd64.zip" | Out-Null
        $pyVersion = $v
        break
    } catch {}
}
if (-not $pyVersion) { throw "No embeddable Python $series.x found on python.org (offline, or not posted yet)" }
"Building piidigger $version ($sha) with Python $pyVersion"

# Clean dist\, keeping earlier build archives
Get-ChildItem dist -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike '*.zip' -and $_.Name -notlike '*.tar.gz' } |
    Remove-Item -Recurse -Force -ErrorAction Stop

# Install from uv.lock.  The project itself isn't installed: the bundle copies src\.
$env:UV_PROJECT_ENVIRONMENT = ".venv-build"
try {
    uv sync --locked --no-dev --extra win --no-install-project --python $pyVersion; Assert-ExitCode "uv sync"
} finally {
    Remove-Item Env:UV_PROJECT_ENVIRONMENT
}

# Runs write_build_info.py itself, then assembles dist\piidigger-amd64
.\.github\scripts\build-windows-embedded.ps1 -arch amd64 -pyVersion $pyVersion -venv .venv-build; Assert-ExitCode "build-windows-embedded.ps1"

# Antivirus can briefly lock files in the new bundle, so retry the zip
foreach ($try in 1..3) {
    try {
        Compress-Archive -ErrorAction Stop -Force -Path dist\piidigger-amd64 -DestinationPath "dist\piidigger-$version-dev-$sha-windows-amd64.zip"
        break
    } catch {
        if ($try -eq 3) { throw }
        Start-Sleep -Seconds 10
    }
}

uv run --no-project python .github\scripts\smoke_test.py --expect-commit $sha -- "$PWD\dist\piidigger-amd64\piidigger.cmd"
Assert-ExitCode "Smoke test"
}
```

If `uv sync` can't find the Python version, that patch is newer than your uv. Run `uv self update` and try again.

### Linux (bash, Docker)

The build runs in the same manylinux2014 container as the release build, so the bundle runs on
glibc 2.17 and later. `--user` makes the container write files you own.

```bash
(
set -euo pipefail
version=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' src/piidigger/__init__.py)
: "${version:?no __version__ in src/piidigger/__init__.py}"
sha=$(git rev-parse --short=7 HEAD)
: "${sha:?git rev-parse failed}"

mkdir -p dist
find dist -mindepth 1 -maxdepth 1 ! -name '*.zip' ! -name '*.tar.gz' -exec rm -rf {} +

# The container has no git, so record the commit on the host first
uv run --no-project python .github/scripts/write_build_info.py

docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD:/io" -w /io \
    -e ARTIFACT_BASE="piidigger-$version-dev-$sha" \
    quay.io/pypa/manylinux2014_x86_64 \
    bash .github/scripts/build-linux-standalone.sh

uv run --no-project python .github/scripts/smoke_test.py --expect-commit "$sha" -- "$PWD/dist/piidigger/piidigger"
)
```

### macOS (bash or zsh)

```bash
(
set -euo pipefail
version=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' src/piidigger/__init__.py)
: "${version:?no __version__ in src/piidigger/__init__.py}"
sha=$(git rev-parse --short=7 HEAD)
: "${sha:?git rev-parse failed}"

mkdir -p dist
find dist -mindepth 1 -maxdepth 1 ! -name '*.zip' ! -name '*.tar.gz' -exec rm -rf {} +

uv run --no-project python .github/scripts/write_build_info.py

# --reinstall-package: never reuse a cached build of piidigger, which could carry a stale _build_info.py
UV_PROJECT_ENVIRONMENT=.venv-build uv sync --locked --no-dev --extra build --no-editable --reinstall-package piidigger
UV_PROJECT_ENVIRONMENT=.venv-build uv run --no-sync pyinstaller --noconfirm --onedir --distpath dist \
    --name piidigger --collect-submodules wakepy src/piidigger/__main__.py
tar -C dist -czf "dist/piidigger-$version-dev-$sha-macos-$(uname -m).tar.gz" piidigger

uv run --no-project python .github/scripts/smoke_test.py --expect-commit "$sha" -- "$PWD/dist/piidigger/piidigger"
)
```

The macOS build is unsigned, so Gatekeeper warns about an unidentified developer when you run it.