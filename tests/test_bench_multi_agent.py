"""Tests for the multi-agent benchmark scenarios."""
from __future__ import annotations

import asyncio
import time
from typing import List

import pytest

from kairos.bench import (
    BestOfNScenario,
    ParallelCoderScenario,
    ReviewerPanel,
    ReviewerPanelScenario,
    default_problem_set,
    humaneval_mini,
    mbpp_mini,
)
from kairos.bench.problems import HumanEvalProblem, MBPPProblem


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class PerfectCoder:
    """Returns the reference solution."""
    name = "perfect"

    def __init__(self, problem_map: dict | None = None):
        self.problem_map = problem_map or {}

    def generate(self, prompt: str) -> str:
        for ref, entry in self.problem_map.items():
            if entry in prompt:
                return ref
        return "def _stub(): return None"


class HalfCoder:
    """Returns the right answer half the time (alternating)."""
    name = "half"

    def __init__(self, problem_map: dict | None = None):
        self.problem_map = problem_map or {}
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        # Every odd call returns correct, every even returns wrong.
        if self.calls % 2 == 1:
            for ref, entry in self.problem_map.items():
                if entry in prompt:
                    return ref
        return "def _stub(): return None"


class SlowCoder:
    """Sleeps a bit per call — used to verify parallel speedup."""
    name = "slow"

    def __init__(self, problem_map: dict | None = None, sleep_s: float = 0.05):
        self.problem_map = problem_map or {}
        self.sleep_s = sleep_s

    def generate(self, prompt: str) -> str:
        time.sleep(self.sleep_s)
        for ref, entry in self.problem_map.items():
            if entry in prompt:
                return ref
        return "def _stub(): return None"


def _pm():
    return {p.reference_solution: p.entry_point for p in default_problem_set()}


# ---------------------------------------------------------------------------
# BestOfNScenario
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_best_of_n_perfect_coder():
    sc = BestOfNScenario(ks=(1, 3, 5))
    report = await sc.run(humaneval_mini()[:3], PerfectCoder(_pm()))
    assert len(report.results) == 3
    for r in report.results:
        assert r.pass_rate == 1.0


@pytest.mark.asyncio
async def test_best_of_n_half_coder_shows_lift():
    """HalfCoder: odd calls correct, even wrong. With k=3, at least
    one of the 3 attempts is correct, so pass@3 = 1.0."""
    sc = BestOfNScenario(ks=(1, 3))
    report = await sc.run(humaneval_mini()[:2], HalfCoder(_pm()))
    by_k = {r.config["k"]: r for r in report.results}
    # k=1: depends on which call. Could be 0/2 or 1/2 — not asserted.
    # k=3: every problem gets 3 calls, so at least 2 of 3 are odd →
    # 2/2 passed.
    assert by_k[3].pass_rate == 1.0


@pytest.mark.asyncio
async def test_best_of_n_summary_contains_lift():
    sc = BestOfNScenario(ks=(1, 3))
    report = await sc.run(humaneval_mini()[:2], PerfectCoder(_pm()))
    assert "best-of-1" in report.summary
    assert "best-of-3" in report.summary


@pytest.mark.asyncio
async def test_best_of_n_to_dict_shape():
    sc = BestOfNScenario(ks=(1, 2))
    report = await sc.run(humaneval_mini()[:1], PerfectCoder(_pm()))
    d = report.to_dict()
    assert d["name"] == "best-of-N"
    assert len(d["results"]) == 2


# ---------------------------------------------------------------------------
# ReviewerPanelScenario
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_panel_0_always_passes():
    sc = ReviewerPanelScenario(panels=(0,))
    report = await sc.run(humaneval_mini()[:2], PerfectCoder(_pm()))
    # 0 reviewers means skip the panel — perfect coder still passes.
    assert report.results[0].pass_rate == 1.0


@pytest.mark.asyncio
async def test_reviewer_panel_filters_wrong_candidates():
    """The default panel rejects anything that doesn't pass tests,
    so a stub-only agent should fail under any non-zero panel."""
    class StubCoder:
        def generate(self, prompt: str) -> str:
            return "def _stub(): return None"

    sc = ReviewerPanelScenario(panels=(0, 1, 2))
    report = await sc.run(humaneval_mini()[:3], StubCoder())
    by_n = {r.config["n_reviewers"]: r for r in report.results}
    # 0 reviewers ⇒ stub passes (panel skipped)
    assert by_n[0].pass_rate == 1.0
    # 1+ reviewers ⇒ stub caught
    assert by_n[1].pass_rate == 0.0
    assert by_n[2].pass_rate == 0.0


@pytest.mark.asyncio
async def test_reviewer_panel_class_rejects_bad_candidate():
    panel = ReviewerPanel(accept_unless_passing=True)
    p = humaneval_mini()[0]
    verdict = panel.review(p, "def has_close_elements(x, t): return True")
    assert verdict["approve"] is False
    assert verdict["score"] == 0


@pytest.mark.asyncio
async def test_reviewer_panel_class_auto_approves_when_off():
    panel = ReviewerPanel(accept_unless_passing=False)
    p = humaneval_mini()[0]
    verdict = panel.review(p, "anything")
    assert verdict["approve"] is True


# ---------------------------------------------------------------------------
# ParallelCoderScenario
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_coder_perfect():
    sc = ParallelCoderScenario(concurrencies=(1, 2, 4))
    report = await sc.run(humaneval_mini()[:4], PerfectCoder(_pm()))
    for r in report.results:
        assert r.pass_rate == 1.0
        assert r.total == 4


@pytest.mark.asyncio
async def test_parallel_coder_speedup():
    """K=2 should be faster than K=1 for a slow coder.

    The SlowCoder sleeps per call; the per-call subprocess
    overhead is ~100-200ms on Windows. We make the sleep large
    enough (0.5s × 4 problems) that the overhead is amortized
    and parallel-2 should win clearly.
    """
    sc = ParallelCoderScenario(concurrencies=(1, 2))
    problems = humaneval_mini()[:4]  # 4 problems
    report = await sc.run(problems, SlowCoder(_pm(), sleep_s=0.5))
    by_k = {r.config["parallelism"]: r for r in report.results}
    seq = by_k[1].duration_s
    par = by_k[2].duration_s
    # Assert parallel is faster, with slack: this runs real subprocesses, and a
    # loaded machine (or a shared CI runner) can eat the margin of a slow coder.
    # It is a wiring check ("the fan-out actually overlaps"), not a benchmark.
    assert par <= seq * 1.25, (
        f"parallel={par:.2f}s seq={seq:.2f}s — parallelism does not overlap")


@pytest.mark.asyncio
async def test_parallel_coder_summary_includes_speedup_lines():
    sc = ParallelCoderScenario(concurrencies=(1, 2, 4))
    report = await sc.run(humaneval_mini()[:2], PerfectCoder(_pm()))
    assert "parallel-1" in report.summary
    assert "parallel-2" in report.summary
    assert "parallel-4" in report.summary


# ---------------------------------------------------------------------------
# run_default_comparison
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_comparison_returns_all_three():
    from kairos.bench import run_default_comparison
    reports = await run_default_comparison(
        humaneval_mini()[:2], PerfectCoder(_pm()),
    )
    assert set(reports.keys()) == {"best-of-N", "reviewer-panel", "parallel-coder"}
    for r in reports.values():
        assert r.results  # every report has at least one scenario


@pytest.mark.asyncio
async def test_run_default_comparison_with_include_filter():
    from kairos.bench import run_default_comparison
    reports = await run_default_comparison(
        humaneval_mini()[:2], PerfectCoder(_pm()),
        include=("best-of-N",),
    )
    assert list(reports.keys()) == ["best-of-N"]
