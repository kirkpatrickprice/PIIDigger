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

UV_VERSION="0.13.6"
# tr drops the \r a Windows checkout (core.autocrlf) leaves behind
PYTHON_VERSION="$(tr -d '[:space:]' < .python-version)"

# RELEASE_TAG is the older interface, kept until the release workflow passes ARTIFACT_BASE.
ARTIFACT_BASE="${ARTIFACT_BASE:-piidigger-${RELEASE_TAG:-dev}}"

export HOME=/tmp/build-home
export UV_CACHE_DIR=/tmp/uv-cache
export UV_PYTHON_INSTALL_DIR=/tmp/uv-python
export UV_PROJECT_ENVIRONMENT=/tmp/build-venv
mkdir -p "$HOME"

curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_INSTALL_DIR=/tmp/uv-bin UV_NO_MODIFY_PATH=1 sh
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
