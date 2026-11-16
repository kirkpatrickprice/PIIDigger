"""The version string `piidigger --version` prints and each run log opens with."""

from __future__ import annotations

import importlib.metadata
import json
import re
import sys
from pathlib import Path

import piidigger
from piidigger import __version__


def full_version() -> str:
    """__version__, plus the commit a build came from when a build wrote one.

    Builds run .github/scripts/write_build_info.py, which writes the
    git-ignored _build_info.py.  A source checkout has none, so it reports the
    version alone.
    """
    try:
        from piidigger._build_info import COMMIT  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415
    except ImportError:
        return __version__
    return f"{__version__} ({COMMIT})" if COMMIT else __version__


def install_kind() -> str:
    """How this copy of PIIDigger was built and installed, which tells support what else it fixed.

    - "embedded": embedded Python running a copy of src, as the Windows
      release does.  Nothing is frozen; the python._pth file, which embedded
      Python reads in place of site, sits beside python.exe.
    - "pyinstaller": a PyInstaller bundle, as the Linux and macOS releases are.
    - "installed": a wheel or sdist installed with pip or uv, from PyPI or a
      downloaded file.  The commit is the release's, but pip or uv chose the
      dependency versions.
    - "source": anything built from a checkout: an editable install, pip
      install of a folder or git URL, or src not installed at all.  Its code
      may differ from the commit _build_info.py names.

    The first two say which build script made the bundle, not whether CI ran it.
    """
    if getattr(sys, "frozen", False):
        return "pyinstaller"
    if sys.platform == "win32" and _beside_embedded_python():
        return "embedded"
    try:
        dist = importlib.metadata.distribution("piidigger")
    except importlib.metadata.PackageNotFoundError:
        return "source"  # src run without installing it
    # Metadata describes the imported copy only if it puts our __init__.py where
    # Python found it.  An editable install's does not, nor does a stray
    # .dist-info elsewhere on sys.path.
    if not _same_file(dist.locate_file("piidigger/__init__.py"), piidigger.__file__):
        return "source"
    try:
        direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:  # malformed direct_url.json: say what the files show
        direct_url = {}
    # PEP 610: a folder or a VCS URL is a local build; a registry install writes no
    # direct_url.json, and a downloaded wheel or sdist writes archive_info.
    if isinstance(direct_url, dict) and ("dir_info" in direct_url or "vcs_info" in direct_url):
        return "source"
    return "installed"


def _beside_embedded_python() -> bool:
    try:
        return any(Path(sys.executable).parent.glob("python*._pth"))
    except OSError:  # an unreadable or empty sys.executable: not embedded
        return False


def _same_file(located: object, imported: str | None) -> bool:
    if imported is None:
        return False
    try:
        return Path(str(located)).resolve() == Path(imported).resolve()
    except OSError:
        return False


def installed_packages() -> list[str]:
    """Each installed package as "name version", sorted by name, PIIDigger itself left out.

    Read from the packages' own metadata, so it reports what this process can
    import rather than what pyproject.toml asks for.  A PyInstaller bundle
    carries that metadata only because its build passes
    --recursive-copy-metadata piidigger.  A source checkout lists its dev tools too.
    """
    found: dict[str, str] = {}
    for dist in importlib.metadata.distributions():  # in sys.path order, so the first copy is the one imported
        name = dist.metadata["Name"]
        key = _normalized(name) if name else ""
        if key and key != "piidigger":
            found.setdefault(key, f"{name} {dist.version}")
    return [found[key] for key in sorted(found)]


def _normalized(name: str) -> str:
    """name as PEP 503 compares it, so typing_extensions and typing-extensions are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()
