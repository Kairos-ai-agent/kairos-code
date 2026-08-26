"""Public benchmarks for Kairos.

Provides a runner that executes Kairos's Coder agent on a set of
standard coding problems (HumanEval / MBPP / custom) and reports
pass@k metrics. The framework is model-agnostic: it only requires
the agent to expose ``.generate(prompt) -> str`` (the same interface
the reflection module already uses).

Layout::

    kairos/bench/
      __init__.py          ← this file
      problems.py          ← canonical problem set (HumanEval-style)
      runner.py            ← orchestration: agent × problem → results
      sandbox_exec.py      ← safe subprocess to run candidate code
      report.py            ← pretty-print + JSON dump
      multi_agent.py       ← best-of-N, reviewer panel, parallel coder

Usage (programmatic)::

    from kairos.bench import BenchmarkRunner, HumanEvalProblem
    from kairos.bench.problems import default_problem_set
    runner = BenchmarkRunner(agent=my_coder)
    result = runner.run(problems=default_problem_set(), k=1)
    print(result.summary())

Usage (CLI)::

    python -m kairos.bench --problems humaneval-mini --k 1
"""
from .runner import BenchmarkRunner, BenchmarkResult, ProblemResult
from .problems import (
    BenchmarkProblem,
    HumanEvalProblem,
    MBPPProblem,
    CustomUnitTestProblem,
    default_problem_set,
    humaneval_mini,
    mbpp_mini,
)
from .sandbox_exec import run_candidate
from .report import format_summary, to_json
from .multi_agent import (
    MultiAgentReport,
    ScenarioResult,
    BestOfNScenario,
    ReviewerPanel,
    ReviewerPanelScenario,
    ParallelCoderScenario,
    run_default_comparison,
)

__all__ = [
    # core
    "BenchmarkRunner",
    "BenchmarkResult",
    "ProblemResult",
    "BenchmarkProblem",
    "HumanEvalProblem",
    "MBPPProblem",
    "CustomUnitTestProblem",
    "default_problem_set",
    "humaneval_mini",
    "mbpp_mini",
    "run_candidate",
    "format_summary",
    "to_json",
    # multi-agent
    "MultiAgentReport",
    "ScenarioResult",
    "BestOfNScenario",
    "ReviewerPanel",
    "ReviewerPanelScenario",
    "ParallelCoderScenario",
    "run_default_comparison",
]
