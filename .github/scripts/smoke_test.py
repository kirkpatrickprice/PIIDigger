"""Run a built PIIDigger and fail unless it reports its commit and finds the card numbers in a few known files.

Catches what unit tests cannot: a bundle missing a module, a DLL or a data file.
The scan covers plain text, ZIP, 7z, PDF, DOCX and XLSX so each reader's
dependencies get loaded.  Standard library only.

    python .github/scripts/smoke_test.py --expect-commit abc1234 -- dist/piidigger/piidigger
    python .github/scripts/smoke_test.py --expect-commit abc1234 --cwd dist/piidigger-amd64 -- piidigger.cmd

COMMAND runs from --cwd (default: a temporary directory, so a checkout's src/
cannot stand in for what the bundle is missing).  A relative COMMAND[0] that
exists under --cwd is resolved there.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # noqa: S404 -- runs the build under test
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PAN_DIR = REPO_ROOT / "testdata" / "pan"

# Each one holds at least one card number the default PAN handler finds.
SAMPLE_FILES = (
    "example-file.txt",
    "example.zip",
    "example-file.7z",
    "sample-pans.pdf",
    "sample-pans.docx",
    "Excel Sheet.xlsx",
)

SCAN_TIMEOUT_SECONDS = 300


def toml_str(value: str) -> str:
    # JSON string escaping is valid TOML basic-string escaping.
    return json.dumps(value)


def write_config(work: Path, scan_dir: Path) -> Path:
    start = toml_str(scan_dir.as_posix())
    config = work / "smoke.toml"
    config.write_text(
        "\n".join(
            [
                'data_handlers = ["pan"]',
                "admin_check = false",
                f"log_file = {toml_str((work / 'logs' / 'piidigger.log').as_posix())}",
                "[start_dirs]",
                f"windows = [{start}]",
                f"macos = [{start}]",
                f"linux = [{start}]",
                "[exclude_dirs]",
                "windows = []",
                "macos = []",
                "linux = []",
                "[results]",
                f"path = {toml_str((work / 'results').as_posix())}",
                'formats = ["json"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def run(command: list[str], cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    full = [*command, *args]
    print(f"$ {' '.join(full)}  (cwd={cwd})", flush=True)
    result = subprocess.run(  # noqa: S603 -- the command under test, no shell
        full, cwd=cwd, capture_output=True, text=True, timeout=SCAN_TIMEOUT_SECONDS, check=False
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result


def found_files(results_json: Path) -> set[str]:
    records = json.loads(results_json.read_text(encoding="utf-8"))
    names: set[str] = set()
    for record in records:
        # Archive members are reported with source_path set to the archive on disk.
        names.add(Path(str(record.get("source_path", ""))).name)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expect-commit", required=True, help="short commit ID --version must report")
    parser.add_argument("--cwd", type=Path, help="directory to run COMMAND from")
    parser.add_argument("command", nargs="+", metavar="COMMAND", help="how to run piidigger (after --)")
    args = parser.parse_args()

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="piidigger-smoke-") as tmp:
        work = Path(tmp)
        cwd = (args.cwd or work).resolve()
        command = list(args.command)
        if (cwd / command[0]).exists():
            command[0] = str(cwd / command[0])

        version = run(command, cwd, "--version")
        if version.returncode != 0:
            failures.append(f"--version exited {version.returncode}")
        elif f"({args.expect_commit})" not in version.stdout:
            failures.append(f"--version does not name commit {args.expect_commit}: {version.stdout.strip()!r}")

        scan_dir = work / "scan"
        scan_dir.mkdir()
        for name in SAMPLE_FILES:
            shutil.copy2(PAN_DIR / name, scan_dir / name)
        config = write_config(work, scan_dir)

        scan = run(command, cwd, "scan", "-f", str(config))
        if scan.returncode != 0:
            failures.append(f"scan exited {scan.returncode}")

        # The JSON sink writes <hostname>-<timestamp>.json (plus a .jsonl stream) into results.path.
        results_json = sorted((work / "results").glob("*.json"))
        if len(results_json) != 1:
            failures.append(f"expected one .json results file in {work / 'results'}, found {len(results_json)}")
        else:
            missing = sorted(set(SAMPLE_FILES) - found_files(results_json[0]))
            if missing:
                failures.append(f"no card numbers reported for: {', '.join(missing)}")

        if failures:
            log = work / "logs" / "piidigger.log"
            if log.is_file():
                print("---- piidigger.log (last 40 lines) ----", file=sys.stderr)
                print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]), file=sys.stderr)

    for failure in failures:
        print(f"SMOKE TEST FAILED: {failure}", file=sys.stderr)
    if not failures:
        print("Smoke test passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
