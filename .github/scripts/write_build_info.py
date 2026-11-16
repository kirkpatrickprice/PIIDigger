"""Write src/piidigger/_build_info.py so `piidigger --version` names the commit a build came from.

Every release and local build runs this before it installs or packages the
project.  The generated file is git-ignored.  Standard library only:

    python .github/scripts/write_build_info.py
"""

from __future__ import annotations

import subprocess  # noqa: S404 -- runs git only, with a fixed argument list
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "src" / "piidigger" / "_build_info.py"

commit = subprocess.run(  # noqa: S603 -- fixed arguments, no shell
    ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=7", "HEAD"],  # noqa: S607 -- git from PATH
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
OUTPUT.write_text(f'COMMIT = "{commit}"  # Written by .github/scripts/write_build_info.py\n', encoding="utf-8")
print(f"Wrote {OUTPUT.relative_to(REPO_ROOT).as_posix()}: COMMIT = {commit}")
