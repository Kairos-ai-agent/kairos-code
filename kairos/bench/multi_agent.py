"""Multi-agent benchmark scenarios.

Measures whether Kairos's multi-agent features (best-of-N Coder
attempts, multiple Reviewers, parallel Coder+Reviewer loops)
actually deliver a pass-rate or latency improvement over a
single-shot baseline.

Each scenario wraps one or more problem sets and produces a
:class:`MultiAgentReport` with the numbers side by side.

Scenarios included:

  - :class:`BestOfNScenario`     — run the same problem N times,
                                   count how many unique solutions
                                   pass at least one. Measures the
                                   diversity lift of best-of-N.
  - :class:`ReviewerPanelScenario` — same problem under different
                                   reviewer configurations
                                   (0 / 1 / 2 / 3 reviewers),
                                   measures the lift of panel review.
  - :class:`ParallelCoderScenario` — split the problem set across
                                   K parallel coders (each solves
                                   its own slice), measure wall
                                   time vs sequential.

All three are model-agnostic; they only require the agents to
expose ``.generate(prompt)`` / ``.agenerate(prompt)`` /
``.review(candidate)`` and follow the :mod:`kairos.bench` contracts.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from .problems import BenchmarkProblem
from .runner import BenchmarkResult, BenchmarkRunner, ProblemResult
from .sandbox_exec import run_candidate

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    """A single configuration's pass@k + wall time."""
    label: str
    config: Dict[str, Any]
    benchmark: BenchmarkResult

    @property
    def passed(self) -> int:
        return self.benchmark.passed

    @property
    def total(self) -> int:
        return self.benchmark.total

    @property
    def pass_rate(self) -> float:
        return self.benchmark.pass_at_k

    @property
    def duration_s(self) -> float:
        return self.benchmark.finished_at - self.benchmark.started_at

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "config": self.config,
            "passed": self.passed,
            "total": self.total,
            "pass_rate": round(self.pass_rate, 4),
            "duration_s": round(self.duration_s, 3),
        }


@dataclass
class MultiAgentReport:
    """Side-by-side comparison of scenarios."""
    name: str
    results: List[ScenarioResult] = field(default_factory=list)
    summary: str = ""

    def add(self, r: ScenarioResult) -> None:
        self.results.append(r)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        if not self.results:
            self.summary = "(no results)"
            return
        rows = []
        for r in self.results:
            rows.append(
                f"  {r.label:<28} pass@k={r.pass_rate:.0%} "
                f"({r.passed}/{r.total}) in {r.duration_s:.1f}s"
            )
        # Compute lift relative to the first result (assumed baseline).
        base = self.results[0]
        if base.pass_rate > 0:
            lift_lines = []
            for r in self.results[1:]:
                delta = r.pass_rate - base.pass_rate
                speedup = (base.duration_s / r.duration_s) if r.duration_s > 0 else 0.0
                lift_lines.append(
                    f"  {r.label:<28} Δpass={delta:+.0%}  speedup={speedup:.2f}x"
                )
            self.summary = "\n".join(rows + ["", "Lift vs baseline:"] + lift_lines)
        else:
            self.summary = "\n".join(rows)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Scenario 1: best-of-N lift
# ---------------------------------------------------------------------------


@dataclass
class BestOfNScenario:
    """Run each problem with k attempts, compare k=1 vs k=3 vs k=5.

    The "lift" is whether best-of-N ever finds a passing solution
    that k=1 misses. Diversity matters: a model that always emits
    the same wrong answer gains nothing.
    """
    name: str = "best-of-N"
    ks: Tuple[int, ...] = (1, 3, 5)

    async def run(
        self, problems: Sequence[BenchmarkProblem], agent: Any,
    ) -> MultiAgentReport:
        report = MultiAgentReport(name=self.name)
        for k in self.ks:
            runner = BenchmarkRunner(agent=agent, label=f"best-of-{k}")
            bench = await runner.run_async(problems, k=k)
            report.add(ScenarioResult(
                label=f"best-of-{k}",
                config={"k": k},
                benchmark=bench,
            ))
        return report


# ---------------------------------------------------------------------------
# Scenario 2: reviewer panel
# ---------------------------------------------------------------------------


class ReviewerPanel:
    """A simple reviewer that approves / rejects based on a heuristic.

    Real reviewers are LLM-backed; for benchmarks we just need
    something deterministic. The default rejects candidates that
    don't pass the test suite — that's the strongest possible
    panel. We can also use no-op panels (always approve) to
    isolate the effect of the Coder alone.
    """

    def __init__(self, *, accept_unless_passing: bool = True,
                 name: str = "test-reviewer") -> None:
        self.accept_unless_passing = accept_unless_passing
        self.name = name

    def review(self, problem: BenchmarkProblem, candidate: str) -> Dict[str, Any]:
        if not self.accept_unless_passing:
            return {"approve": True, "score": 100, "summary": "auto-approve"}
        ok, msg = run_candidate(problem, candidate)
        return {
            "approve": ok,
            "score": 100 if ok else 0,
            "summary": msg[:200],
        }


@dataclass
class ReviewerPanelScenario:
    """Compare 0 / 1 / 2 / 3 reviewers in series."""
    name: str = "reviewer-panel"
    panels: Tuple[int, ...] = (0, 1, 2, 3)

    async def run(
        self, problems: Sequence[BenchmarkProblem], agent: Any,
    ) -> MultiAgentReport:
        report = MultiAgentReport(name=self.name)
        for n in self.panels:
            bench = await self._run_with_panel(problems, agent, n)
            report.add(ScenarioResult(
                label=f"{n}-reviewer",
                config={"n_reviewers": n},
                benchmark=bench,
            ))
        return report

    async def _run_with_panel(
        self, problems: Sequence[BenchmarkProblem], agent: Any, n: int,
    ) -> BenchmarkResult:
        """Apply the panel *before* scoring. A candidate must survive
        all N reviewers to count as 'passed'."""
        results: List[ProblemResult] = []
        started = time.time()
        for problem in problems:
            t0 = time.perf_counter()
            try:
                if hasattr(agent, "agenerate"):
                    cand = await agent.agenerate(problem.prompt)
                else:
                    cand = agent.generate(problem.prompt)
            except Exception as exc:  # noqa: BLE001
                results.append(ProblemResult(
                    problem_id=problem.id, name=problem.name,
                    passed=False, attempts=1, error=str(exc),
                    duration_s=time.perf_counter() - t0,
                ))
                continue
            # Apply reviewers in series. n=0 means skip review.
            approved = True
            last_msg = ""
            for i in range(max(n, 1)) if n > 0 else []:
                reviewer = ReviewerPanel(name=f"r{i}")
                verdict = reviewer.review(problem, cand)
                if not verdict["approve"]:
                    approved = False
                    last_msg = verdict.get("summary", "")
                    break
            results.append(ProblemResult(
                problem_id=problem.id, name=problem.name,
                passed=approved, attempts=1,
                details=[(approved, last_msg or "ok")],
                duration_s=time.perf_counter() - t0,
            ))
        return BenchmarkResult(
            started_at=started, finished_at=time.time(),
            k=1, agent_label=f"panel-{n}", results=results,
        )


# ---------------------------------------------------------------------------
# Scenario 3: parallel coders
# ---------------------------------------------------------------------------


@dataclass
class ParallelCoderScenario:
    """Split the problem set across K parallel coders.

    Each coder solves its own slice of the problem set. The
    wall-clock time is dominated by the slowest coder. We compare
    K=1 (sequential) vs K=2/3/4 (parallel).
    """
    name: str = "parallel-coder"
    concurrencies: Tuple[int, ...] = (1, 2, 3, 4)

    async def run(
        self, problems: Sequence[BenchmarkProblem], agent: Any,
    ) -> MultiAgentReport:
        report = MultiAgentReport(name=self.name)
        for k in self.concurrencies:
            bench = await self._run_parallel(problems, agent, k)
            report.add(ScenarioResult(
                label=f"parallel-{k}",
                config={"parallelism": k},
                benchmark=bench,
            ))
        return report

    async def _run_parallel(
        self, problems: Sequence[BenchmarkProblem], agent: Any, parallelism: int,
    ) -> BenchmarkResult:
        # Round-robin assign problems to N workers.
        workers = [[] for _ in range(parallelism)]
        for i, p in enumerate(problems):
            workers[i % parallelism].append(p)

        async def _run_slice(slice_problems):
            local: List[ProblemResult] = []
            for problem in slice_problems:
                t0 = time.perf_counter()
                try:
                    if hasattr(agent, "agenerate"):
                        cand = await agent.agenerate(problem.prompt)
                    else:
                        cand = agent.generate(problem.prompt)
                    ok, msg = run_candidate(problem, cand)
                    local.append(ProblemResult(
                        problem_id=problem.id, name=problem.name,
                        passed=ok, attempts=1, details=[(ok, msg)],
                        duration_s=time.perf_counter() - t0,
                    ))
                except Exception as exc:  # noqa: BLE001
                    local.append(ProblemResult(
                        problem_id=problem.id, name=problem.name,
                        passed=False, attempts=1, error=str(exc),
                        duration_s=time.perf_counter() - t0,
                    ))
            return local

        started = time.time()
        slices = await asyncio.gather(*[_run_slice(w) for w in workers])
        results = [r for s in slices for r in s]
        return BenchmarkResult(
            started_at=started, finished_at=time.time(),
            k=1, agent_label=f"parallel-{parallelism}", results=results,
        )


# ---------------------------------------------------------------------------
# Convenience: run a default multi-agent comparison
# ---------------------------------------------------------------------------


async def run_default_comparison(
    problems: Sequence[BenchmarkProblem], agent: Any,
    *,
    include: Optional[Tuple[str, ...]] = None,
) -> Dict[str, MultiAgentReport]:
    """Run all three scenarios and return a dict {name: report}."""
    scenarios = {
        "best-of-N": BestOfNScenario(),
        "reviewer-panel": ReviewerPanelScenario(),
        "parallel-coder": ParallelCoderScenario(),
    }
    if include:
        scenarios = {k: v for k, v in scenarios.items() if k in include}
    out: Dict[str, MultiAgentReport] = {}
    for name, sc in scenarios.items():
        logger.info("multi-agent scenario: %s", name)
        out[name] = await sc.run(problems, agent)
    return out
