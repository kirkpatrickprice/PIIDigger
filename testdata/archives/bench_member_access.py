#!/usr/bin/env python3
"""Measure what it costs to reach archive members one task at a time versus in batches.

Run from the repository root:
    uv run python -P testdata/archives/bench_member_access.py
    uv run python -P testdata/archives/bench_member_access.py --members 500 1000 --workers 8

Everything happens in memory, so disk speed does not affect the numbers.  The
script uses tarfile, py7zr and zipfile directly rather than PIIDigger's archive
handlers, so the same access patterns can be compared before and after a
handler change.

Access patterns, per format and member count N:
    one pass     decompress the whole archive once (T_d)
    per member   open the archive once per member, as one task per member does
                 (timed on an evenly spaced sample, then scaled up to N)
    K batches    split the members into K contiguous runs; each run opens the
                 archive once, skips forward to its first member and reads to
                 its last.  Times are summed, i.e. total worker time.

The last section measures R = T_s / T_d: how many times longer the enabled data
handlers take to scan some text than gzip and xz take to decompress it.  R is
what decides how much repeated decompression costs relative to scanning.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import io
import lzma
import random
import tarfile
import time
import zipfile
from collections.abc import Callable

import py7zr
from py7zr.io import HashIOFactory

from piidigger.datahandlers import HANDLER_REGISTRY

_SAMPLE = 20  # members timed for the per-member estimate


def _member_data(rng: random.Random, size: int) -> bytes:
    """Text-like, partly compressible content of roughly `size` bytes."""
    return base64.b64encode(rng.randbytes(size * 3 // 4))


def _names(n: int) -> list[str]:
    return [f"dir/f{i:05d}.txt" for i in range(n)]


def build_tar_gz(n: int, size: int) -> bytes:
    rng = random.Random(1)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in _names(n):
            data = _member_data(rng, size)
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def build_7z(n: int, size: int) -> bytes:
    rng = random.Random(1)
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as szf:
        for name in _names(n):
            szf.writestr(_member_data(rng, size), name)
    return buf.getvalue()


def build_zip(n: int, size: int) -> bytes:
    rng = random.Random(1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _names(n):
            zf.writestr(name, _member_data(rng, size))
    return buf.getvalue()


# -- tar ----------------------------------------------------------------------


def tar_one_pass(blob: bytes) -> None:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r|*") as tf:
        for info in tf:
            if info.isfile():
                extracted = tf.extractfile(info)
                if extracted is not None:
                    extracted.read()


def tar_per_member(blob: bytes, name: str) -> None:
    """The pattern one task per member used: getmember() then extract."""
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tf:
        extracted = tf.extractfile(tf.getmember(name))
        if extracted is not None:
            extracted.read()


def tar_batch(blob: bytes, names: list[str]) -> None:
    """Walk forward with next(): skip the prefix once, stop after the last wanted member."""
    wanted = set(names)
    last = names[-1]
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tf:
        while (info := tf.next()) is not None:
            if info.name in wanted:
                extracted = tf.extractfile(info)
                if extracted is not None:
                    extracted.read()
                if info.name == last:
                    break


# -- 7z -----------------------------------------------------------------------


def sevenz_one_pass(blob: bytes) -> None:
    with py7zr.SevenZipFile(io.BytesIO(blob), "r") as szf:
        szf.extract(factory=HashIOFactory())


def sevenz_per_member(blob: bytes, name: str) -> None:
    with py7zr.SevenZipFile(io.BytesIO(blob), "r") as szf:
        szf.extract(targets=[name], factory=HashIOFactory())


def sevenz_batch(blob: bytes, names: list[str]) -> None:
    with py7zr.SevenZipFile(io.BytesIO(blob), "r") as szf:
        szf.extract(targets=names, factory=HashIOFactory())


# -- zip ----------------------------------------------------------------------


def zip_one_pass(blob: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            zf.read(info)


def zip_per_member(blob: bytes, name: str) -> None:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        zf.read(name)


def zip_batch(blob: bytes, names: list[str]) -> None:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in names:
            zf.read(name)


_FORMATS: dict[
    str, tuple[Callable[[int, int], bytes], Callable[..., None], Callable[..., None], Callable[..., None]]
] = {
    "tar.gz": (build_tar_gz, tar_one_pass, tar_per_member, tar_batch),
    "7z (solid)": (build_7z, sevenz_one_pass, sevenz_per_member, sevenz_batch),
    "zip": (build_zip, zip_one_pass, zip_per_member, zip_batch),
}


def _timed(fn: Callable[[], None]) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def _split(names: list[str], k: int) -> list[list[str]]:
    """K contiguous runs of near-equal length."""
    size, extra = divmod(len(names), k)
    runs, start = [], 0
    for i in range(k):
        end = start + size + (1 if i < extra else 0)
        runs.append(names[start:end])
        start = end
    return runs


def bench_access(members: list[int], member_kb: int, workers: int) -> None:
    size = member_kb * 1000
    print(f"Member access cost  (members of {member_kb} KB, totals are summed worker time)")
    header = f"{'format':<11} {'N':>6} {'archive':>8} {'one pass':>9} {'per member':>11} {'K=1':>8} {f'K={workers}':>8} {'K=N/10':>8}"
    print(header)
    print("-" * len(header))
    for label, (build, one_pass, per_member, batch) in _FORMATS.items():
        for n in members:
            blob = build(n, size)
            names = _names(n)
            t_pass = _timed(lambda: one_pass(blob))  # noqa: B023 - called immediately
            step = max(1, n // _SAMPLE)
            sample = names[::step]
            per = sum(_timed(lambda nm=nm: per_member(blob, nm)) for nm in sample) / len(sample) * n  # noqa: B023
            cols = []
            for k in (1, workers, max(1, n // 10)):
                runs = _split(names, min(k, n))
                cols.append(sum(_timed(lambda r=r: batch(blob, r)) for r in runs))  # noqa: B023
            print(
                f"{label:<11} {n:>6} {len(blob) / 1e6:>6.1f}MB {t_pass:>8.2f}s {per:>10.1f}s"
                f" {cols[0]:>7.2f}s {cols[1]:>7.2f}s {cols[2]:>7.2f}s"
            )
    print()


def bench_ratio(text_mb: int) -> None:
    rng = random.Random(1)
    lines = []
    total = 0
    i = 0
    while total < text_mb * 1_000_000:
        line = (
            f'{{"ts":"2026-10-03T12:{i % 60:02d}:00Z","user":"u{i}@example.com",'
            f'"msg":"{base64.b64encode(rng.randbytes(60)).decode()}","n":{rng.randint(10**12, 10**16)}}}\n'
        )
        lines.append(line)
        total += len(line)
        i += 1
    text = "".join(lines).encode()
    mb = len(text) / 1e6

    handlers = list(HANDLER_REGISTRY.values())

    def scan() -> None:
        for line in lines:
            for dh in handlers:
                dh.find_matches(line)

    gz = gzip.compress(text, 6)
    xz = lzma.compress(text)
    t_scan = _timed(scan)
    t_gz = _timed(lambda: gzip.decompress(gz))
    t_xz = _timed(lambda: lzma.decompress(xz))

    print(f"Scan vs decompress  ({mb:.0f} MB of JSON-log text, handlers: {', '.join(HANDLER_REGISTRY)})")
    print(f"  scan        {mb / t_scan:8.1f} MB/s")
    print(f"  gunzip      {mb / t_gz:8.1f} MB/s   R = {t_scan / t_gz:6.0f}")
    print(f"  xz / LZMA   {mb / t_xz:8.1f} MB/s   R = {t_scan / t_xz:6.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--members", type=int, nargs="+", default=[250, 500, 1000], help="member counts to test")
    parser.add_argument("--member-kb", type=int, default=20, help="size of each member in KB")
    parser.add_argument("--workers", type=int, default=8, help="K for the 'K = workers' column")
    parser.add_argument("--text-mb", type=int, default=10, help="text size for the scan-vs-decompress ratio")
    args = parser.parse_args()
    bench_access(args.members, args.member_kb, args.workers)
    bench_ratio(args.text_mb)


if __name__ == "__main__":
    main()
