#!/bin/bash
# Runs *inside* the quay.io/pypa/manylinux2014_x86_64 container (invoked via
# `docker run` from the `linux` job in _release-build-standalone.yml, or by hand
# for a local build -- see CONTRIBUTING.md) to freeze a PyInstaller build
# against glibc 2.17 for broad Linux compatibility.
#
# The image's own /opt/python/* interpreters are built without a shared
# libpython (by design, for wheel building), and PyInstaller requires one.
# Instead, uv installs a python-build-standalone CPython, which ships
# libpython3.14.so and itself targets glibc 2.17. The manylinux2014 container
# still supplies the glibc 2.17 userland that PyInstaller collects from.
#
# Run .github/scripts/write_build_info.py on the host first: the container has
# no git. Everything uv writes lives under /tmp, so the container can run as
# the host user (`docker run --user`) and leave files the host user owns.
set -euo pipefail

# To update uv, change both lines. The hash is in
# https://github.com/astral-sh/uv/releases/download/<version>/uv-x86_64-unknown-linux-gnu.tar.gz.sha256
# Also change every setup-uv `version:` in .github/workflows/_release-*.yml, so all builds use one uv.
UV_VERSION="0.13.6"
UV_SHA256="f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645"
# tr drops the \r a Windows checkout (core.autocrlf) leaves behind
PYTHON_VERSION="$(tr -d '[:space:]' < .python-version)"

: "${ARTIFACT_BASE:?set ARTIFACT_BASE, e.g. piidigger-2.0.0-dev-c11dff2}"

export HOME=/tmp/build-home
export UV_CACHE_DIR=/tmp/uv-cache
export UV_PYTHON_INSTALL_DIR=/tmp/uv-python
export UV_PROJECT_ENVIRONMENT=/tmp/build-venv
mkdir -p "$HOME" /tmp/uv-bin

# A hash-checked release download rather than piping an install script to sh.
curl -fsSLo /tmp/uv.tar.gz \
    "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c -
tar -xzf /tmp/uv.tar.gz -C /tmp/uv-bin --strip-components=1
export PATH="/tmp/uv-bin:$PATH"

# --reinstall-package: never reuse a cached build of piidigger, which could carry a stale _build_info.py.
uv sync --locked --no-dev --extra build --no-editable --reinstall-package piidigger \
    --python "$PYTHON_VERSION" --managed-python

# Entry is the package's __main__.py, not the repo-root piidigger.py: PyInstaller
# puts the script's directory first on its search path, and a root-level
# piidigger.py shadows the piidigger package so none of it gets bundled.
uv run --no-sync pyinstaller --noconfirm --onedir --distpath dist --name piidigger --collect-submodules wakepy \
    src/piidigger/__main__.py

ARTIFACT="${ARTIFACT_BASE}-linux-x86_64-manylinux2014.tar.gz"
tar -C dist -czf "dist/$ARTIFACT" piidigger

echo "Built dist/${ARTIFACT}"
