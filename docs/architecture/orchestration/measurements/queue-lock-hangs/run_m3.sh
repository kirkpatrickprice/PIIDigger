#!/usr/bin/env bash
# M3 driver: real scans with 5 random SIGKILLs each.
# Run from the root of a repo clone on Linux/macOS after `uv sync`.
# DATA must hold the scan tree (see README.md); SCRATCH is wiped per trial.
# Appends one JSON line per trial to $OUT (default ./m3_results.jsonl).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-$PWD/.venv/bin/python}
OUT=${OUT:-$PWD/m3_results.jsonl}
DATA=${DATA:-$HOME/m3data}
SCRATCH=${SCRATCH:-$HOME/m3out}
FAST_TRIALS=${FAST_TRIALS:-40}
BALANCED_TRIALS=${BALANCED_TRIALS:-30}

[ -d "$DATA" ] || { echo "DATA directory not found: $DATA" >&2; exit 1; }

trial() {  # preset first_kill spacing timeout
    rm -rf "$SCRATCH"
    local line
    line=$(timeout $(( ${4%.*} + 60 )) "$PY" "$HERE/m3_random_kill_scan.py" "$DATA" "$SCRATCH" "$1" 5 "$2" "$3" "$4" 2>/dev/null | grep '^{' | tail -1)
    [ -z "$line" ] && line='{"outcome":"harness-error","preset":"'"$1"'"}'
    echo "$line" >> "$OUT"
}

# Kill timings fit the 11,525-file subset: ~21 s at fast, ~31 s at balanced.
# Rescale first_kill/spacing/timeout if DATA differs.
for i in $(seq "$FAST_TRIALS"); do trial fast 3 2.5 90; done
for i in $(seq "$BALANCED_TRIALS"); do trial balanced 4 3.5 120; done
rm -rf "$SCRATCH"
