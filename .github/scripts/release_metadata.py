"""Work out a release run's version, commit and artifact name, and stop a release whose tag, version or pre-release box disagree.

The `metadata` job in _release-verify.yml runs this.  It reads GITHUB_EVENT_NAME
and, on a release event, RELEASE_TAG and RELEASE_PRERELEASE ("true" or "false").
It writes version, commit, is_prerelease, artifact_base and python_version to
GITHUB_OUTPUT.  Standard library only:

    python .github/scripts/release_metadata.py

A published release must be tagged v<__version__>, in canonical PEP 440 form
(2.0.0, 2.0.0rc1), and its "Set as a pre-release" box must match the version.
Any other run is a dry run, named <version>-dev-<commit>.
"""

from __future__ import annotations

import os
import re
import subprocess  # noqa: S404 -- runs git only, with a fixed argument list
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Canonical PEP 440 public versions only: no epoch, no local part, no
# alternative spellings (PyPI would normalize 2.0.0-rc1 to 2.0.0rc1, and the tag would no longer match).
NUMBER = r"(?:0|[1-9]\d*)"
VERSION_RE = re.compile(
    rf"^{NUMBER}(?:\.{NUMBER})*(?P<pre>(?:a|b|rc){NUMBER})?(?:\.post{NUMBER})?(?P<dev>\.dev{NUMBER})?$"
)


def package_version() -> str:
    init = (REPO_ROOT / "src" / "piidigger" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE)
    if not match:
        sys.exit("::error::No __version__ in src/piidigger/__init__.py")
    return match.group(1)


def head_commit() -> str:
    return subprocess.run(  # noqa: S603 -- fixed arguments, no shell
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=7", "HEAD"],  # noqa: S607 -- git from PATH
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main() -> int:
    version = package_version()
    commit = head_commit()
    match = VERSION_RE.match(version)
    if not match:
        print(f"::error::__version__ {version!r} is not a canonical PEP 440 version such as 2.0.0 or 2.0.0rc1")
        return 1
    is_prerelease = bool(match.group("pre") or match.group("dev"))

    if os.environ.get("GITHUB_EVENT_NAME") == "release":
        tag = os.environ["RELEASE_TAG"]
        if tag != f"v{version}":
            print(
                f"::error::Release tag {tag!r} does not match piidigger.__version__ {version!r} (expected v{version})"
            )
            return 1
        box = os.environ["RELEASE_PRERELEASE"] == "true"
        if box != is_prerelease:
            kind = "a pre-release" if is_prerelease else "a full release"
            state = "ticked" if box else "not ticked"
            print(f"::error::{version} is {kind}, but the release's 'Set as a pre-release' box is {state}")
            return 1
        artifact_base = f"piidigger-{version}-{commit}"
    else:
        artifact_base = f"piidigger-{version}-dev-{commit}"

    outputs = {
        "version": version,
        "commit": commit,
        "is_prerelease": str(is_prerelease).lower(),
        "artifact_base": artifact_base,
        # strip() also drops the \r a Windows checkout (core.autocrlf) leaves behind
        "python_version": (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip(),
    }
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        for key, value in outputs.items():
            out.write(f"{key}={value}\n")
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
