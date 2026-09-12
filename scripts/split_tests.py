#!/usr/bin/env python
"""Split the test suite into balanced shards so CI can run it in parallel.

The full suite takes ~3 minutes on a many-core workstation but 40+ on a 2-vCPU
GitHub runner, because a large share of the 1843 tests spawn subprocesses (the
demo CLI tests alone run the entire gate a dozen times). One job would either
time out or take the better part of an hour; three balanced shards finish in
roughly a third of that.

    python scripts/split_tests.py --shard 1 --shards 3     # paths, one per line
    python scripts/split_tests.py --verify                 # coverage + balance report

Weight = the number of `def test_` functions in the file, so a shard's cost
tracks the actual test count rather than the line count. Assignment is greedy
(largest first) and fully deterministic: same input, same shards, every time.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_COUNT = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)


def test_files() -> dict[str, int]:
    """Every collected test module, mapped to its weight."""
    files: dict[str, int] = {}
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        files[rel] = max(1, len(TEST_COUNT.findall(path.read_text(encoding="utf-8", errors="replace"))))
    return files


def assign(files: dict[str, int], shards: int) -> list[list[str]]:
    buckets: list[list[str]] = [[] for _ in range(shards)]
    loads = [0] * shards
    # Largest first, then by name so the result is stable.
    for name, weight in sorted(files.items(), key=lambda kv: (-kv[1], kv[0])):
        target = loads.index(min(loads))
        buckets[target].append(name)
        loads[target] += weight
    return [sorted(b) for b in buckets]


def main() -> int:
    parser = argparse.ArgumentParser(prog="split_tests.py")
    parser.add_argument("--shard", type=int, help="1-based shard index to print")
    parser.add_argument("--shards", type=int, default=3, help="total number of shards")
    parser.add_argument("--verify", action="store_true",
                        help="report the coverage and balance of every shard")
    args = parser.parse_args()

    files = test_files()
    total = sum(files.values())
    buckets = assign(files, args.shards)

    # Emit LF even on Windows: the output is consumed by a shell/pytest, and a
    # stray carriage return makes every path invalid.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")

    if args.verify:
        seen: list[str] = []
        print(f"=== {len(files)} test modules / {total} tests into {args.shards} shards ===")
        for i, bucket in enumerate(buckets, 1):
            weight = sum(files[f] for f in bucket)
            print(f"  shard {i}: {len(bucket):3} files, {weight:4} tests "
                  f"({weight / total * 100:.0f}%)")
            seen += bucket
        assert sorted(seen) == sorted(files), "a test module was lost or duplicated"
        assert len(seen) == len(set(seen)), "a test module is in two shards"
        spread = max(sum(files[f] for f in b) for b in buckets) / min(
            sum(files[f] for f in b) for b in buckets)
        print(f"  coverage: every module exactly once ✓   worst/best balance: {spread:.2f}x")
        return 0

    if not args.shard or not 1 <= args.shard <= args.shards:
        print(f"error: --shard must be 1..{args.shards}", file=sys.stderr)
        return 2
    for name in buckets[args.shard - 1]:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
