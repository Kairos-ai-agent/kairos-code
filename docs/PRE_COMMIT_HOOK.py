#!/usr/bin/env python3
"""Pre-commit hook for kairos exec.

Drop this file at `.git/hooks/pre-commit` (or invoke it from a
project's pre-commit framework) to run the Team Leader on staged
changes before each commit. Any CRITICAL issues reported cause the
commit to be aborted.

This is intentionally a thin wrapper around the `kairos exec` CLI
so the actual review logic is shared between interactive runs,
CI, and git hooks.

Example `.pre-commit-config.yaml` entry:

    repos:
      - repo: local
        hooks:
          - id: kairos-review
            name: kairos review
            entry: python .kairos/pre_commit_review.py
            language: system
            stages: [pre-commit]
            always_run: true
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _staged_files() -> list[str]:
    """Return the list of files staged for commit (added/modified/
    renamed, no deletes). Empty list means there's nothing to
    review — we exit 0."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if out.returncode != 0:
        return []
    return [f for f in out.stdout.splitlines() if f.strip()]


def _has_kairos() -> bool:
    return shutil.which("kairos") is not None or shutil.which("python") is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run kairos exec on staged changes before commit."
    )
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="Max seconds for the kairos run (default: 120).",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Forward --json to kairos exec.",
    )
    args = parser.parse_args()

    staged = _staged_files()
    if not staged:
        # Nothing to review; let the commit proceed.
        return 0

    if not _has_kairos():
        print(
            "pre_commit_review: kairos not on PATH; skipping review",
            file=sys.stderr,
        )
        return 0

    task = (
        f"Review the following staged files for security, "
        f"correctness, and style issues. Report any CRITICAL or "
        f"MAJOR issues. Files:\n"
        + "\n".join(f"  - {f}" for f in staged)
    )

    cmd = [
        "python", "-m", "kairos", "exec", task,
        "--timeout", str(args.timeout),
        "--quiet",
    ]
    if args.json_output:
        cmd.append("--json")

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        # Don't auto-fail the commit on a kairos crash; let the
        # user decide. Just log it.
        print(
            f"pre_commit_review: kairos exec exited {proc.returncode}",
            file=sys.stderr,
        )
        print(proc.stdout, file=sys.stderr)
        return 0

    # Parse the output. With --json we can grep for CRITICAL; with
    # human output we just look at the result string.
    out = proc.stdout
    if args.json_output:
        try:
            data = json.loads(out)
            result = (data.get("result") or "").upper()
        except json.JSONDecodeError:
            result = out.upper()
    else:
        result = out.upper()

    if "CRITICAL" in result:
        print(
            "pre_commit_review: CRITICAL issues found — "
            "commit aborted. Run `kairos exec ...` interactively "
            "to see the full review.",
            file=sys.stderr,
        )
        # Print the review for the user.
        print(out, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
