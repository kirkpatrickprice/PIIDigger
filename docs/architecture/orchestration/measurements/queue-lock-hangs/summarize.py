"""Summarize M1 or M3 result files.

Usage:
    summarize.py m1 m1_results.jsonl
    summarize.py m3 m3_results.jsonl [kills_per_trial]

M3 reports the per-kill hang rate p from the trial survival rate, assuming
independent kills: survival = (1 - p) ** kills.  The 95% range comes from a
Wilson interval on the trial hang rate.
"""

from __future__ import annotations

import collections
import json
import math
import sys
from pathlib import Path


def _rows(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _per_kill(trial_hang_rate: float, kills: int) -> float:
    return 1 - (1 - trial_hang_rate) ** (1 / kills)


def summarize_m1(rows: list[dict]) -> None:
    cells: dict[tuple, collections.Counter] = collections.OrderedDict()
    since: dict[tuple, list[float]] = collections.defaultdict(list)
    for r in rows:
        key = (r.get("grid"), r.get("children"), r.get("arm"), r.get("crash_delay_ms"))
        c = cells.setdefault(key, collections.Counter())
        c["n"] += 1
        c[r.get("outcome")] += 1
        if r.get("big_runs", 1) > 1:
            c["result_rerun"] += 1
        if "abort" in r:
            since[key].append(r["abort"]["since_result_ms"])
    print(f"{'grid':4} {'children':>8} {'arm':5} {'delay':>5}  {'crash ms':>8}  {'hung':>7}  rerun")
    for key, c in cells.items():
        times = sorted(since[key])
        median = f"{times[len(times) // 2]:.1f}" if times else "-"
        print(
            f"{key[0]:4} {key[1]:>8} {key[2]:5} {key[3]:>5}  {median:>8}  {c['hang']:>3}/{c['n']:<3}  {c['result_rerun']}"
        )


def summarize_m3(rows: list[dict], kills: int) -> None:
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        groups[r.get("preset", "?")].append(r)
    groups["combined"] = rows
    for preset, rs in groups.items():
        hung = sum(1 for r in rs if r.get("outcome") == "hang")
        errors = sum(1 for r in rs if r.get("outcome") == "harness-error")
        n = len(rs) - errors
        lo, hi = _wilson(hung, n)
        kinds = collections.Counter(r.get("hang_kind") for r in rs if r.get("outcome") == "hang")
        exits = collections.Counter(r.get("exit_code") for r in rs if r.get("outcome") == "ok")
        print(
            f"{preset:9} trials={n:3} hung={hung:3}  per-kill={_per_kill(hung / n, kills):.1%} "
            f"(95% {_per_kill(lo, kills):.1%}-{_per_kill(hi, kills):.1%})  "
            f"kinds={dict(kinds)} exit_codes={dict(exits)} harness_errors={errors}"
        )


if __name__ == "__main__":
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "m1":
        summarize_m1(_rows(path))
    else:
        summarize_m3(_rows(path), int(sys.argv[3]) if len(sys.argv) > 3 else 5)
