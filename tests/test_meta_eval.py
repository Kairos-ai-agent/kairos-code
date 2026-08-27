"""Meta-eval — exercise the eval framework against itself.

The :mod:`kairos.eval` module is the foundation of our regression
detection. If a grader silently breaks (e.g. regex always returns
0.0, or contains stops matching), every regression suite in the
project gives a false sense of safety. This test runs the
``examples/eval_eval.yaml`` suite against a stub target that
returns deterministic outputs, and asserts each grader's
behavior is what we documented.

If you change a grader, the meta-eval will catch you — update the
suite to match the new behavior before landing the change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from kairos.eval import load_suite, run_suite


REPO_ROOT = Path(__file__).resolve().parent.parent
META_SUITE = REPO_ROOT / "examples" / "eval_eval.yaml"


def _stub_target(prompt: str) -> Dict[str, Any]:
    """Echo the prompt as output and a fast duration so the
    latency grader passes."""
    return {
        "output": prompt,
        "tools_called": ["write_file"],
        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
        "duration_ms": 1,
    }


@pytest.fixture
def meta_suite() -> Dict[str, Any]:
    assert META_SUITE.exists(), (
        f"meta-eval suite missing at {META_SUITE} — has it been moved?"
    )
    return load_suite(META_SUITE)


def test_meta_suite_has_expected_cases(meta_suite):
    """The meta-eval suite covers the 6 graders (plus extras)."""
    names = {c["name"] for c in meta_suite["cases"]}
    expected = {
        "contains-grader-positive", "contains-grader-negative",
        "regex-grader-positive", "regex-grader-negative",
        "exact-grader-positive", "exact-grader-negative",
        "latency-grader-fast",
        "tools-called-grader-positive", "tools-called-grader-missing",
    }
    missing = expected - names
    assert not missing, f"meta-eval missing cases: {missing}"


def test_meta_eval_against_stub_passes():
    """When the stub returns the prompt as output, all positive
    graders should pass and all negative ones should fail. The
    suite pass_rate should reflect that mix."""
    spec = load_suite(META_SUITE)
    result = run_suite(spec, target=_stub_target)
    # Build a name→score map for the assertions below
    by_name = {c.name: c for c in result.cases}
    # Positive cases pass
    assert by_name["contains-grader-positive"].score == 1.0
    assert by_name["regex-grader-positive"].score == 1.0
    assert by_name["exact-grader-positive"].score == 1.0
    assert by_name["latency-grader-fast"].score == 1.0
    assert by_name["tools-called-grader-positive"].score == 1.0
    # Negative cases fail
    assert by_name["contains-grader-negative"].score == 0.0
    assert by_name["regex-grader-negative"].score == 0.0
    assert by_name["exact-grader-negative"].score == 0.0
    assert by_name["tools-called-grader-missing"].score == 0.0
    # Suite pass rate: 5/9 ≈ 0.556
    assert result.pass_rate == pytest.approx(5 / 9)


def test_meta_eval_target_exception_handling():
    """If the target raises, the case fails but the suite keeps going."""
    def target(prompt: str):
        if "RAISE" in str(prompt):
            raise RuntimeError("kaboom")
        return {"output": "x", "tools_called": [],
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                "duration_ms": 1}
    spec = {
        "name": "target-errors",
        "cases": [
            {"name": "raises", "input": "RAISE this prompt", "graders": []},
            {"name": "ok", "input": "fine prompt", "graders": []},
        ],
    }
    result = run_suite(spec, target=target)
    # First case has the error in case.error; second passes (no graders)
    assert result.cases[0].error == "kaboom"
    assert result.cases[0].passed is False
    assert result.cases[1].error is None
    # No graders → score is 1.0 → passes
    assert result.cases[1].score == 1.0
    assert result.cases[1].passed is True
