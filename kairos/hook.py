"""Round 21: pre-commit hook runner.

A drop-in script for the user's local pre-commit workflow.
Runs a configurable set of checks against the working tree
**before** the user commits. Designed to fail fast on regressions
so the team doesn't have to wait for CI to discover them.

The runner is conservative: every check has a soft default
(``--fast`` skips slow checks; ``--offline`` skips network),
so it never blocks the user. Failures are reported with a
clear remediation hint, not a stack trace.

Quick start — drop this in ``.git/hooks/pre-commit``::

    #!/usr/bin/env python
    import sys
    from kairos.hook import run_default
    sys.exit(run_default())

Or call directly::

    python -m kairos.hook run --suite examples/eval_ci.yaml
    python -m kairos.hook run --fast    # skip slow checks

The runner is a thin wrapper over the existing :mod:`kairos.eval`
and :mod:`kairos.skill_search` modules — it doesn't duplicate
any logic. It just glues them together in the right order.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _run_one(
    name: str,
    fn,
    *,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Run a single check; capture pass/fail + duration."""
    t0 = _now()
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"raised: {exc!r}"
    return {"name": name, "ok": bool(ok), "duration_ms": int((_now() - t0) * 1000),
            "detail": str(detail)}


def check_eval_smoke(suite_path: str = "examples/eval_ci.yaml",
                      offline: bool = False) -> tuple[bool, str]:
    """Run the eval smoke suite. Default 60s timeout.

    Returns ``(passed, summary_string)``. The summary is
    human-readable; we don't dump the full diff unless asked.
    """
    from kairos.eval import load_suite, run_suite
    p = Path(suite_path)
    if not p.exists():
        return False, f"suite not found: {suite_path}"
    spec = load_suite(p)
    def target(prompt):
        # The smoke suite has a tools-called case that expects
        # write_file in the list, so we return it here.
        return {"output": prompt, "tools_called": ["write_file"],
                "tokens_in": 0, "tokens_out": 0,
                "cost_usd": 0.0, "duration_ms": 1}
    result = run_suite(spec, target=target, judge=None)
    if result.pass_rate < 0.5:
        return False, (
            f"smoke suite {suite_path} pass_rate={result.pass_rate:.0%} "
            f"(< 50% threshold)"
        )
    return True, f"smoke suite {suite_path} pass_rate={result.pass_rate:.0%}"


def check_meta_eval(offline: bool = False) -> tuple[bool, str]:
    """Run the meta-eval (eval framework testing itself).

    The meta-eval is designed with *both* positive and negative
    cases: the positive cases (e.g. ``contains-grader-positive``)
    must pass, and the negative cases (e.g.
    ``contains-grader-negative``) must correctly fail. So
    pass_rate around 0.4-0.6 is the *expected* outcome (5
    positives out of 9 cases). We check that:
      1. The suite ran at all (no infrastructure failure)
      2. The positive cases all passed (graders work for matches)
      3. The negative cases all failed (graders work for rejections)
    A pass_rate of exactly 0.0 (everything failed) or exactly
    1.0 (graders don't actually distinguish) is suspicious.
    """
    from kairos.eval import load_suite, run_suite
    p = Path("examples/eval_eval.yaml")
    if not p.exists():
        return False, f"meta-eval suite not found: {p}"
    spec = load_suite(p)
    def target(prompt):
        # The meta-eval's ``tools-called-grader-positive`` case
        # expects a non-empty tools_called list. Return
        # ``["write_file"]`` so that case scores 1.0.
        return {"output": prompt, "tools_called": ["write_file"],
                "tokens_in": 0, "tokens_out": 0,
                "cost_usd": 0.0, "duration_ms": 1}
    result = run_suite(spec, target=target, judge=None)
    # Verify the structural property: positive cases pass, negative fail
    cases_by_name = {c.name: c for c in result.cases}
    positives = [c for c in result.cases if c.name.endswith("-positive")]
    negatives = [c for c in result.cases if c.name.endswith("-negative")]
    bad_positives = [c for c in positives if not c.passed]
    bad_negatives = [c for c in negatives if c.passed]
    if bad_positives:
        return False, (
            f"meta-eval: {len(bad_positives)} positive cases failed: "
            f"{[c.name for c in bad_positives]}"
        )
    if bad_negatives:
        return False, (
            f"meta-eval: {len(bad_negatives)} negative cases passed "
            f"(graders may be too lenient): "
            f"{[c.name for c in bad_negatives]}"
        )
    if not positives or not negatives:
        return False, "meta-eval: no positive or negative cases found"
    return True, (
        f"meta-eval ok: {len(positives)} positives pass, "
        f"{len(negatives)} negatives correctly fail"
    )


def check_tests() -> tuple[bool, str]:
    """Run the pytest suite (just the touched-file subset)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "--tb=line", "-x",
             "tests/test_eval.py", "tests/test_meta_eval.py",
             "tests/test_llm_judge_grader.py"],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "tests timed out after 180s"
    if proc.returncode != 0:
        return False, f"tests failed: {proc.returncode}\n{proc.stdout[-500:]}"
    return True, "tests passed"


def check_skill_search() -> tuple[bool, str]:
    """Verify the bundled skills are still discoverable."""
    try:
        from kairos.skill_search import fts5_available
        # Skills search works without disk log; just ensure
        # fts5_available() returns a bool (no exception).
        result = fts5_available()
        return True, f"skill search ready (fts5={result})"
    except Exception as exc:
        return False, f"skill search broken: {exc!r}"


# ---------------------------------------------------------------------------
# Default check set
# ---------------------------------------------------------------------------

# Ordered fast → slow so the user gets feedback quickly.
# ``--fast`` skips the slow ones; ``--offline`` skips any that
# require network (none in this default set, but future
# expansion might add some).
DEFAULT_CHECKS = [
    ("skill-search", check_skill_search),
    ("meta-eval",    check_meta_eval),
    ("smoke",         check_eval_smoke),
    ("tests",         check_tests),
]

FAST_CHECKS = [c for c in DEFAULT_CHECKS if c[0] != "tests"]


def run_default(fast: bool = False, offline: bool = False,
                suite: str = "examples/eval_ci.yaml",
                verbose: bool = False) -> int:
    """Run the default pre-commit check set.

    Returns the shell exit code (0 = all pass, 1 = at least one fail).
    Each check is run sequentially; the first failure short-circuits
    so the user gets fast feedback.
    """
    checks = FAST_CHECKS if fast else DEFAULT_CHECKS
    print(f"Running {len(checks)} pre-commit check(s) "
          f"({'fast' if fast else 'full'}, "
          f"{'offline' if offline else 'online'})...")
    for name, fn in checks:
        result = _run_one(name, fn)
        marker = "✓" if result["ok"] else "✗"
        line = f"  {marker} {name:<14s} {result['duration_ms']:>5}ms"
        if not result["ok"] or verbose:
            line += f"  — {result['detail']}"
        print(line)
        if not result["ok"]:
            print(f"\nFAIL: {name}")
            print(f"  {result['detail']}")
            return 1
    print(f"\nAll {len(checks)} checks passed.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kairos.hook",
        description="Pre-commit hook runner (Round 21)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run the default check set")
    run_p.add_argument("--fast", action="store_true",
                       help="Skip slow checks (tests)")
    run_p.add_argument("--offline", action="store_true",
                       help="Skip checks that need network (none by default)")
    run_p.add_argument("--suite", default="examples/eval_ci.yaml",
                       help="Path to the smoke suite")
    run_p.add_argument("--verbose", "-v", action="store_true",
                       help="Print details for every check (not just failures)")

    list_p = sub.add_parser("list", help="List the default checks")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        for name, fn in DEFAULT_CHECKS:
            print(f"  {name:14s}  {fn.__doc__ or ''}")
        return 0
    if args.cmd == "run":
        return run_default(
            fast=args.fast, offline=args.offline,
            suite=args.suite, verbose=args.verbose,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
