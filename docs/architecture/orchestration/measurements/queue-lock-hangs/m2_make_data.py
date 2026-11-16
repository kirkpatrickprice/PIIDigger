"""Build a file-share-like tree for M2.

- share/: 1,500 dirs x 30 files of 2-40 KB text; ~10% hold a few PANs/emails
- bigdirs/: dirs of 2,000, 5,000 and 20,000 files (large ENUM_DIR results)
- dense/: 20 files with 2,000-10,000 findings each (large SCAN_FILE results)
- docs/: 5 copies of the repo's testdata (docx/xlsx/pdf/zip/7z/tar/jsonl...)
"""

from __future__ import annotations

import random
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
testdata = Path(sys.argv[2])
rng = random.Random(42)  # noqa: S311 - test data, not security
PANS = ["4111111111111111", "5500005555555559", "340000000000009", "6011000000000004"]
WORDS = "invoice account balance customer payment order shipping contact note review quarterly report".split()


def text(kb: int, findings: int) -> str:
    lines = []
    size = 0
    while size < kb * 1024:
        line = " ".join(rng.choice(WORDS) for _ in range(12))
        lines.append(line)
        size += len(line) + 1
    for _ in range(findings):
        i = rng.randrange(len(lines))
        extra = rng.choice(PANS) if rng.random() < 0.5 else f"user{rng.randrange(99999)}@example.com"
        lines[i] += " " + extra
    return "\n".join(lines)


if root.exists():
    shutil.rmtree(root)
for d in range(1500):
    p = root / "share" / f"dept{d % 40:02d}" / f"folder{d:04d}"
    p.mkdir(parents=True)
    for f in range(30):
        n = rng.randint(1, 5) if rng.random() < 0.10 else 0
        (p / f"doc{f:02d}.txt").write_text(text(rng.randint(2, 40), n))
for count in (2000, 5000, 20000):
    p = root / "bigdirs" / f"dir{count}"
    p.mkdir(parents=True)
    for f in range(count):
        (p / f"scan_{f:06d}.txt").write_text(text(1, 0))
p = root / "dense"
p.mkdir(parents=True)
for f in range(20):
    (p / f"export{f:02d}.txt").write_text(text(200, rng.randint(2000, 10000)))
for c in range(5):
    shutil.copytree(testdata, root / "docs" / f"copy{c}", ignore=shutil.ignore_patterns("*.py"))
print(sum(1 for _ in root.rglob("*") if _.is_file()), "files")
