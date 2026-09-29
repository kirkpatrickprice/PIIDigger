#!/bin/bash
# Runs *inside* the quay.io/pypa/manylinux2014_x86_64 container (invoked via
# `docker run` from the `linux` job in _release-build-standalone.yml) to
# freeze a PyInstaller build against glibc 2.17 for broad Linux compatibility.
set -euo pipefail

CPYTHON_DIR="/opt/python/cp314-cp314"

if [ ! -d "$CPYTHON_DIR" ]; then
  echo "::error::cp314 not found under /opt/python/ in quay.io/pypa/manylinux2014_x86_64." \
       "Available interpreters: $(ls /opt/python/ 2>/dev/null || echo none). " \
       "Python 3.14 may not yet be built into this manylinux2014 image; " \
       "see docs/refactor for the accepted fallback options." >&2
  exit 1
fi

export PATH="$CPYTHON_DIR/bin:$PATH"

TAG="${RELEASE_TAG:-dev}"

python -m pip install --upgrade pip
python -m pip install .[build]

pyinstaller --onedir --distpath dist -i piidigger.ico --collect-submodules wakepy piidigger.py

ARTIFACT="piidigger-${TAG}-linux-x86_64-manylinux2014.tar.gz"
tar -C dist -czf "$ARTIFACT" piidigger
mv "$ARTIFACT" dist/

echo "Built dist/${ARTIFACT}"
