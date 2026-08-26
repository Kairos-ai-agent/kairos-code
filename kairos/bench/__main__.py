"""CLI for the benchmark runner.

Usage::

    python -m kairos.bench --problems humaneval-mini --k 1
    python -m kairos.bench --problems all --k 1 --json out.json
    python -m kairos.bench --problems humaneval-mini --mock
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List

from .problems import (
    BenchmarkProblem,
    default_problem_set,
    humaneval_mini,
    mbpp_mini,
)
from .runner import BenchmarkRunner
from .report import format_summary, to_json


class MockCoder:
    """A canned agent that always returns the reference solution.

    Used for end-to-end pipeline testing (no model required) and as
    a sanity check that the harness can run, score, and report.
    """
    name = "mock-coder"

    def __init__(self, behavior: str = "perfect"):
        # behavior: "perfect" | "wrong" | "half"
        self.behavior = behavior

    def generate(self, prompt: str) -> str:
        # The harness only needs *something*; the runner scores
        # whatever the agent emits. We don't have the reference
        # here, so we return a no-op stub that compiles. Real
        # tests should use a proper model.
        return "def _stub():\n    return None\n"


def _select_problems(name: str) -> List[BenchmarkProblem]:
    if name == "humaneval-mini":
        return humaneval_mini()
    if name == "mbpp-mini":
        return mbpp_mini()
    if name in ("all", "default"):
        return default_problem_set()
    raise SystemExit(f"unknown problem set: {name}")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Kairos benchmarks.")
    parser.add_argument("--problems", default="all",
                        choices=["humaneval-mini", "mbpp-mini", "all", "default"])
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--label", default="cli")
    parser.add_argument("--mock", action="store_true",
                        help="use a mock agent (for pipeline testing)")
    parser.add_argument("--json", metavar="PATH",
                        help="write JSON report to this file")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))

    if args.mock:
        agent = MockCoder(behavior="wrong")
    else:
        # Real model usage: the user wires their own provider
        # (OpenAI / Anthropic / Ollama) via kairos.providers.
        print(
            "ERROR: no model provider is wired up yet. "
            "Use --mock to test the pipeline, or pass your own agent "
            "to BenchmarkRunner programmatically.",
            file=sys.stderr,
        )
        return 2

    problems = _select_problems(args.problems)
    runner = BenchmarkRunner(agent=agent, label=args.label)
    result = runner.run(problems, k=args.k)
    print(format_summary(result))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(to_json(result))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    main()
