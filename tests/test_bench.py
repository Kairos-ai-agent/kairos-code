"""Tests for the kairos.bench framework."""
from __future__ import annotations

import json
import time

import pytest

from kairos.bench import (
    BenchmarkRunner,
    BenchmarkResult,
    HumanEvalProblem,
    MBPPProblem,
    default_problem_set,
    humaneval_mini,
    mbpp_mini,
)
from kairos.bench.sandbox_exec import run_candidate, _strip_code_fence


# ---------------------------------------------------------------------------
# Problem-set metadata
# ---------------------------------------------------------------------------


def test_humaneval_mini_has_5_problems():
    ps = humaneval_mini()
    assert len(ps) == 5
    for p in ps:
        assert isinstance(p, HumanEvalProblem)
        assert p.entry_point
        assert p.tests


def test_mbpp_mini_has_5_problems():
    ps = mbpp_mini()
    assert len(ps) == 5
    for p in ps:
        assert isinstance(p, MBPPProblem)
        assert p.entry_point
        assert p.tests


def test_default_problem_set_combines():
    assert len(default_problem_set()) == 10


def test_problem_to_dict_roundtrips():
    p = humaneval_mini()[0]
    d = p.to_dict()
    assert d["id"] == p.id
    assert d["entry_point"] == p.entry_point
    assert d["tests"] == p.tests


# ---------------------------------------------------------------------------
# Sandbox execution
# ---------------------------------------------------------------------------


def test_strip_code_fence_plain():
    assert _strip_code_fence("def f(): return 1") == "def f(): return 1"


def test_strip_code_fence_python():
    raw = "```python\ndef f(): return 1\n```"
    assert _strip_code_fence(raw) == "def f(): return 1"


def test_strip_code_fence_no_lang():
    raw = "```\ndef f(): return 1\n```"
    assert _strip_code_fence(raw) == "def f(): return 1"


def test_run_candidate_passes_correct_solution():
    p = humaneval_mini()[0]  # has_close_elements
    ok, msg = run_candidate(p, p.reference_solution)
    assert ok, msg
    assert "passed" in msg


def test_run_candidate_fails_wrong_solution():
    p = humaneval_mini()[0]
    ok, msg = run_candidate(p, "def has_close_elements(numbers, threshold): return True")
    # Always returning True fails the empty list test.
    assert not ok
    assert "tests passed" in msg or "fail" in msg.lower() or msg  # something wrong


def test_run_candidate_handles_compile_error():
    p = humaneval_mini()[0]
    ok, msg = run_candidate(p, "this is not python @@@")
    assert not ok
    assert "COMPILE_ERROR" in msg or "SyntaxError" in msg


def test_run_candidate_handles_missing_entry_point():
    p = humaneval_mini()[0]
    # No function named correctly
    ok, msg = run_candidate(p, "def other_function(): return 1")
    assert not ok
    assert "MISSING_ENTRY" in msg


def test_run_candidate_handles_infinite_loop_with_timeout():
    p = humaneval_mini()[0]
    # Stub timeout to 1.0s so the test doesn't take forever.
    p.timeout_s = 1.0
    code = "def has_close_elements(numbers, threshold):\n    while True: pass\n"
    ok, msg = run_candidate(p, code)
    assert not ok
    assert "timeout" in msg.lower() or "TimeoutError" in msg


def test_run_candidate_strips_fence():
    p = humaneval_mini()[0]
    fenced = "```python\n" + p.reference_solution + "\n```"
    ok, _ = run_candidate(p, fenced)
    assert ok


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class PerfectCoder:
    """A canned agent that returns the reference solution."""
    name = "perfect"

    def __init__(self, problem_map: dict | None = None):
        self.problem_map = problem_map or {}

    def generate(self, prompt: str) -> str:
        # Look up the right reference by entry-point.
        for ref, entry in self.problem_map.items():
            if entry in prompt:
                return ref
        return "def _stub(): return None"


class FailingCoder:
    name = "fail"

    def generate(self, prompt: str) -> str:
        return "def _stub(): return None"


def _make_perfect_coder():
    pm = {p.reference_solution: p.entry_point for p in default_problem_set()}
    return PerfectCoder(pm)


def test_runner_pass_at_1_with_perfect_coder():
    runner = BenchmarkRunner(agent=_make_perfect_coder(), label="perfect")
    result = runner.run(default_problem_set(), k=1)
    assert isinstance(result, BenchmarkResult)
    assert result.total == 10
    assert result.passed == 10
    assert result.pass_at_1 == 1.0
    assert result.pass_at_k == 1.0


def test_runner_zero_pass_with_failing_coder():
    runner = BenchmarkRunner(agent=FailingCoder(), label="fail")
    result = runner.run(humaneval_mini()[:2], k=1)
    assert result.passed == 0
    assert result.pass_at_1 == 0.0


def test_runner_pass_at_k_with_half_correct():
    """With k=2, even one correct attempt out of two counts as pass."""
    class HalfCoder:
        def __init__(self):
            self.calls = 0
        def generate(self, prompt):
            self.calls += 1
            # First call: stub (wrong). Second: real.
            if self.calls % 2 == 0:
                return humaneval_mini()[0].reference_solution
            return "def _stub(): return None"

    agent = HalfCoder()
    runner = BenchmarkRunner(agent=agent, label="half")
    result = runner.run([humaneval_mini()[0]], k=2)
    assert result.passed == 1
    assert result.pass_at_1 == 0.0  # first attempt was wrong
    assert result.pass_at_k == 1.0  # second attempt saved it


def test_runner_summary_shape():
    runner = BenchmarkRunner(agent=_make_perfect_coder(), label="shape")
    result = runner.run(humaneval_mini()[:3], k=1)
    s = result.summary()
    for key in ("agent", "k", "total", "passed", "pass_at_1", "pass_at_k",
                "duration_s", "total_prompt_tokens", "total_completion_tokens"):
        assert key in s
    assert s["agent"] == "shape"
    assert s["k"] == 1
    assert s["total"] == 3


def test_runner_records_token_usage():
    runner = BenchmarkRunner(agent=_make_perfect_coder(), label="tokens")
    result = runner.run(humaneval_mini()[:2], k=1)
    for r in result.results:
        # The default char/4 estimator must produce something > 0
        assert r.prompt_tokens > 0
        assert r.completion_tokens > 0


def test_runner_records_error_message_on_failure():
    runner = BenchmarkRunner(agent=FailingCoder(), label="err")
    result = runner.run(humaneval_mini()[:1], k=1)
    assert result.results[0].error
    # The failing coder returns "_stub" which doesn't define the entry
    # point, so the runner reports MISSING_ENTRY. Either error form is
    # acceptable — we just verify *some* error got recorded.
    err = result.results[0].error
    assert "MISSING_ENTRY" in err or "no output" in err or "0/" in err


def test_runner_to_dict_serializable():
    runner = BenchmarkRunner(agent=_make_perfect_coder(), label="json")
    result = runner.run(humaneval_mini()[:1], k=1)
    s = json.dumps(result.to_dict())
    parsed = json.loads(s)
    assert "summary" in parsed or "agent" in parsed
    assert len(parsed["results"]) == 1


def test_runner_rejects_invalid_k():
    runner = BenchmarkRunner(agent=FailingCoder(), label="x")
    with pytest.raises(ValueError):
        runner.run(humaneval_mini()[:1], k=0)


def test_runner_with_concurrent_semaphore():
    """max_concurrency>1 should still produce correct results."""
    runner = BenchmarkRunner(agent=_make_perfect_coder(),
                             label="concurrent", max_concurrency=4)
    result = runner.run(default_problem_set(), k=1)
    assert result.passed == 10
