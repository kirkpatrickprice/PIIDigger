#!/bin/bash
# Runs *inside* the quay.io/pypa/manylinux2014_x86_64 container (invoked via
# `docker run` from the `linux` job in _release-build-standalone.yml) to
# freeze a PyInstaller build against glibc 2.17 for broad Linux compatibility.
#
# The image's own /opt/python/* interpreters are built without a shared
# libpython (by design, for wheel building), and PyInstaller requires one.
# Instead, uv installs a python-build-standalone CPython, which ships
# libpython3.14.so and itself targets glibc 2.17. The manylinux2014 container
# still supplies the glibc 2.17 userland that PyInstaller collects from.
set -euo pipefail

UV_VERSION="0.13.1"
PYTHON_VERSION="3.14"

TAG="${RELEASE_TAG:-dev}"

curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_INSTALL_DIR=/usr/local/bin sh

uv venv --python "$PYTHON_VERSION" --managed-python /tmp/build-venv
export VIRTUAL_ENV=/tmp/build-venv
export PATH="$VIRTUAL_ENV/bin:$PATH"

uv pip install ".[build]"

# Entry is the package's __main__.py, not the repo-root piidigger.py: PyInstaller
# puts the script's directory first on its search path, and a root-level
# piidigger.py shadows the piidigger package so none of it gets bundled.
pyinstaller --onedir --distpath dist --name piidigger --collect-submodules wakepy src/piidigger/__main__.py

ARTIFACT="piidigger-${TAG}-linux-x86_64-manylinux2014.tar.gz"
tar -C dist -czf "$ARTIFACT" piidigger
mv "$ARTIFACT" dist/

echo "Built dist/${ARTIFACT}"
