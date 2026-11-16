"""The version string `piidigger --version` prints and each run log opens with."""

from __future__ import annotations

import importlib.metadata
import re

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
