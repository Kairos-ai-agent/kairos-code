"""Tests for kairos.eval (agent regression framework)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from kairos.eval import (
    CaseResult,
    ContainsGrader,
    CostGrader,
    ExactGrader,
    LatencyGrader,
    RegexGrader,
    SuiteResult,
    ToolCallGrader,
    build_graders,
    compare_runs,
    load_suite,
    load_suite_result,
    run_suite,
    save_suite_result,
    score_output,
    _welch_t,
)


# ---------------------------------------------------------------------------
# Grader unit tests
# ---------------------------------------------------------------------------


def test_contains_grader_all_present():
    g = ContainsGrader(["def ", "return", "string"])
    # "def reverse(s: str) -> str:  # string helper" — contains all 3
    src = "def reverse(s: str) -> str:  # string helper\n    return s"
    score, det = g.score(src, {})
    assert score == 1.0
    assert det["hits"] == 3


def test_contains_grader_partial():
    g = ContainsGrader(["def ", "return", "magic"])
    score, det = g.score("def reverse(s):\n    return s", {})
    assert score == pytest.approx(2 / 3)
    assert det["hits"] == 2


def test_contains_grader_case_insensitive():
    g = ContainsGrader(["FOO"], case_sensitive=False)
    assert g.score("foo bar", {})[0] == 1.0
    g2 = ContainsGrader(["FOO"], case_sensitive=True)
    assert g2.score("foo bar", {})[0] == 0.0


def test_regex_grader_match():
    g = RegexGrader(r"def\s+\w+\(.*\)\s*->")
    assert g.score("def reverse(s: str) -> str:", {})[0] == 1.0
    assert g.score("class Foo:", {})[0] == 0.0


def test_exact_grader():
    g = ExactGrader("hello")
    assert g.score("hello", {})[0] == 1.0
    assert g.score("Hello", {})[0] == 0.0
    g2 = ExactGrader("hello", case_sensitive=False)
    assert g2.score("Hello", {})[0] == 1.0


def test_latency_grader_in_budget():
    g = LatencyGrader(max_ms=1000)
    assert g.score("out", {"duration_ms": 500})[0] == 1.0
    assert g.score("out", {"duration_ms": 1000})[0] == 1.0


def test_latency_grader_over_budget():
    g = LatencyGrader(max_ms=1000)
    # 1500ms = 50% over budget → 0.5
    assert g.score("out", {"duration_ms": 1500})[0] == 0.5
    # 2000ms = 2x budget → 0
    assert g.score("out", {"duration_ms": 2000})[0] == 0.0


def test_cost_grader_in_budget():
    g = CostGrader(max_usd=0.01)
    assert g.score("out", {"cost_usd": 0.005})[0] == 1.0


def test_cost_grader_over_budget():
    g = CostGrader(max_usd=0.01)
    assert g.score("out", {"cost_usd": 0.015})[0] == pytest.approx(0.5)


def test_tool_call_grader_require_all():
    g = ToolCallGrader(["search", "write_file"], require_all=True)
    assert g.score("", {"tools_called": ["search", "write_file", "bash"]})[0] == 1.0
    assert g.score("", {"tools_called": ["search"]})[0] == 0.0


def test_tool_call_grader_any():
    g = ToolCallGrader(["search", "write_file"], require_all=False)
    assert g.score("", {"tools_called": ["search"]})[0] == 1.0
    assert g.score("", {"tools_called": ["bash"]})[0] == 0.0


# ---------------------------------------------------------------------------
# build_graders
# ---------------------------------------------------------------------------


def test_build_graders_dispatches_by_key():
    gs = build_graders([
        {"contains": ["a", "b"]},
        {"regex": r"\d+"},
        {"exact": "yes"},
        {"latency_max_ms": 1000},
        {"max_usd": 0.01},
        {"tools_called": ["bash"]},
    ])
    assert len(gs) == 6
    types = [type(g).__name__ for g in gs]
    assert types == [
        "ContainsGrader", "RegexGrader", "ExactGrader",
        "LatencyGrader", "CostGrader", "ToolCallGrader",
    ]


def test_build_graders_unknown_warns(caplog):
    build_graders([{"nonsense_key": "x"}])
    # Warning was emitted
    assert any("Unknown grader spec" in r.message for r in caplog.records)


def test_score_output_averages():
    score, details = score_output(
        "hello world",
        [
            ContainsGrader(["hello"]),  # 1.0
            ContainsGrader(["missing"]),  # 0.0
        ],
        {},
    )
    assert score == 0.5
    assert "contains" in details


def test_score_output_empty_graders():
    score, _ = score_output("anything", [], {})
    assert score == 1.0


# ---------------------------------------------------------------------------
# run_suite with a stub target
# ---------------------------------------------------------------------------


def _stub_target_factory(outputs: List[str]):
    """Create a target callable that returns a fixed list of outputs in order."""
    it = iter(outputs)

    def target(prompt: str) -> Dict:
        try:
            out = next(it)
        except StopIteration:
            out = ""
        return {
            "output": out,
            "tools_called": ["bash"],
            "tokens_in": 10, "tokens_out": 20,
            "cost_usd": 0.001, "duration_ms": 100,
        }
    return target


def test_run_suite_all_pass():
    spec = {
        "name": "demo",
        "cases": [
            {"name": "c1", "input": "p1",
             "graders": [{"contains": ["hello"]}]},
            {"name": "c2", "input": "p2",
             "graders": [{"regex": r"world"}]},
        ],
    }
    result = run_suite(spec, target=_stub_target_factory(["hello world", "world"]))
    assert len(result.cases) == 2
    assert all(c.passed for c in result.cases)
    assert result.pass_rate == 1.0


def test_run_suite_partial_pass():
    spec = {
        "name": "demo",
        "cases": [
            {"name": "c1", "input": "p1",
             "graders": [{"contains": ["hello"]}]},
            {"name": "c2", "input": "p2",
             "graders": [{"contains": ["will_not_match"]}]},
        ],
    }
    result = run_suite(spec, target=_stub_target_factory(["hello", "world"]))
    assert result.cases[0].passed is True
    assert result.cases[1].passed is False
    assert result.pass_rate == 0.5


def test_run_suite_target_exception_captured():
    def bad(prompt: str):
        raise RuntimeError("kaboom")
    spec = {"name": "x", "cases": [{"name": "c1", "input": "p1", "graders": []}]}
    result = run_suite(spec, target=bad)
    assert result.cases[0].passed is False
    assert "kaboom" in result.cases[0].error


# ---------------------------------------------------------------------------
# Welch's t-test
# ---------------------------------------------------------------------------


def test_welch_t_identical_distributions_p_close_to_1():
    t, p = _welch_t([0.8, 0.9, 0.85], [0.8, 0.9, 0.85])
    # Identical samples → p close to 1
    assert p > 0.5


def test_welch_t_very_different_p_close_to_0():
    # Use samples with non-zero variance (otherwise se=0 → undefined).
    t, p = _welch_t([0.0, 0.1, 0.0], [1.0, 0.9, 1.0])
    assert p < 0.01


def test_welch_t_too_small_samples_returns_safe():
    t, p = _welch_t([1.0], [0.0, 0.0])
    # n < 2 → no significant result, p=1
    assert p == 1.0


# ---------------------------------------------------------------------------
# compare_runs
# ---------------------------------------------------------------------------


def _make_run(name: str, run_id: str, scores_by_case: Dict[str, float]
              ) -> SuiteResult:
    cases = [
        CaseResult(name=n, score=s, passed=s >= 0.5)
        for n, s in scores_by_case.items()
    ]
    return SuiteResult(suite_name=name, run_id=run_id, cases=cases)


def test_compare_runs_flags_regressions():
    base = _make_run("s", "A", {"c1": 1.0, "c2": 1.0, "c3": 1.0})
    target = _make_run("s", "B", {"c1": 0.0, "c2": 1.0, "c3": 1.0})
    diff = compare_runs(base, target, alpha=0.05)
    assert diff["base_pass_rate"] == 1.0
    assert diff["target_pass_rate"] == pytest.approx(2 / 3)
    assert len(diff["regressions"]) == 1
    assert diff["regressions"][0]["name"] == "c1"


def test_compare_runs_no_diff_returns_empty():
    base = _make_run("s", "A", {"c1": 0.8, "c2": 0.9})
    target = _make_run("s", "B", {"c1": 0.8, "c2": 0.9})
    diff = compare_runs(base, target, alpha=0.05)
    assert diff["regressions"] == []
    assert diff["improvements"] == []
    assert diff["n_common"] == 2


# ---------------------------------------------------------------------------
# Suite load / save round-trip
# ---------------------------------------------------------------------------


def test_suite_load_single_suite(tmp_path: Path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "name: smoke\n"
        "cases:\n"
        "  - name: a\n"
        "    input: 'hi'\n"
        "    graders: [{contains: [hi]}]\n",
        encoding="utf-8",
    )
    spec = load_suite(p)
    assert spec["name"] == "smoke"
    assert len(spec["cases"]) == 1


def test_suite_load_multi_suite_returns_first(tmp_path: Path):
    p = tmp_path / "multi.yaml"
    p.write_text(
        "suites:\n"
        "  - name: first\n"
        "    cases: [{name: c1, input: i, graders: []}]\n"
        "  - name: second\n"
        "    cases: [{name: c2, input: i, graders: []}]\n",
        encoding="utf-8",
    )
    spec = load_suite(p)
    assert spec["name"] == "first"


def test_suite_result_round_trip(tmp_path: Path):
    res = _make_run("s", "r1", {"c1": 0.9, "c2": 0.1})
    p = tmp_path / "out.json"
    save_suite_result(res, p)
    loaded = load_suite_result(p)
    assert loaded.run_id == "r1"
    assert len(loaded.cases) == 2
    assert loaded.cases[0].name == "c1"
    assert loaded.cases[0].score == 0.9
    assert loaded.pass_rate == 0.5
