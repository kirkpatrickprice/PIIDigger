#!/usr/bin/env bash
# M1 grid driver: crash a worker right after it sends a result.
# Run from the root of a repo clone on Linux/macOS after `uv sync`.
# Appends one JSON line per trial to $OUT (default ./m1_results.jsonl).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
H=$HERE/m1_crash_after_result.py
OUT=${OUT:-$PWD/m1_results.jsonl}
PY=${PY:-$PWD/.venv/bin/python}

trial() {  # grid label, then harness args
    local grid=$1; shift
    local line
    line=$(timeout 150 "$PY" "$H" "$@" 2>/dev/null | grep '^{' | tail -1)
    if [ -z "$line" ]; then line='{"outcome":"harness-error"}'; fi
    echo "{\"grid\":\"$grid\",${line#\{}" >> "$OUT"
}

# A: window profile vs result size (8 workers, light background load)
for children in 1000 10000 50000; do
    to=15; [ "$children" = 50000 ] && to=40
    for d in 0 0.002 0.005 0.010 0.015 0.020 0.030 0.050 0.100; do
        for i in $(seq 10); do
            trial A --workers 8 --children "$children" --crash-delay "$d" --timeout "$to"
        done
    done
done

# B: control - crash right after a SMALL result
for d in 0 0.002 0.005; do
    for i in $(seq 30); do
        trial B --workers 8 --children 1000 --arm small --crash-delay "$d" --timeout 15
    done
done

# C: saturated coordinator (15 workers, 20k zero-sleep background tasks)
for d in 0.005 0.010 0.020 0.050 0.100 0.200; do
    for i in $(seq 10); do
        trial C --workers 15 --children 10000 --bg 20000 --bg-sleep 0 --crash-delay "$d" --timeout 60
    done
done
