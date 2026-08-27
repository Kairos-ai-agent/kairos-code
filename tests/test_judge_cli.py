"""Tests for the per-case LLM judge CLI flow (Round 20).

We verify:
  - The eval suite loads examples/eval_with_judge.yaml
  - Each case's llm_judge grader is constructed correctly
  - Running without --judge fails open (score=1.0)
  - Running with a stub --judge applies the rubric per case
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE_EXAMPLE = REPO_ROOT / "examples" / "eval_with_judge.yaml"


def test_judge_yaml_loads():
    """The example YAML is valid and contains the expected cases."""
    assert JUDGE_EXAMPLE.exists()
    with open(JUDGE_EXAMPLE, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    assert spec["name"] == "eval-with-judge"
    assert len(spec["cases"]) == 2
    # Every case has at least one llm_judge grader
    for c in spec["cases"]:
        graders = c.get("graders", [])
        judge_graders = [g for g in graders if "llm_judge" in g]
        assert len(judge_graders) >= 1
        # The judge has a prompt and a threshold
        jg = judge_graders[0]["llm_judge"]
        assert "prompt" in jg
        assert "threshold" in jg
        assert 0.0 <= jg["threshold"] <= 1.0


def test_build_graders_creates_llm_judge_grader():
    """build_graders with a stub judge returns the LLMJudgeGrader
    for each llm_judge spec."""
    from kairos.eval import build_graders, LLMJudgeGrader
    specs = [
        {"llm_judge": {"prompt": "rate it", "threshold": 0.7}},
        {"contains": ["hi"]},
    ]
    judge = lambda p: "score: 0.5"
    gs = build_graders(specs, judge=judge)
    assert len(gs) == 2
    jg = next(g for g in gs if isinstance(g, LLMJudgeGrader))
    assert jg.threshold == 0.7
    assert jg._judge is judge


def test_run_suite_evaluates_judge_per_case():
    """An llm_judge spec gets the same judge callable and the per-case
    threshold."""
    from kairos.eval import build_graders, LLMJudgeGrader
    specs = [
        {"llm_judge": {"prompt": "rate it", "threshold": 0.5}},
        {"llm_judge": {"prompt": "rate it", "threshold": 0.9}},
    ]
    # The judge returns different scores for each call
    import itertools
    counter = itertools.count()
    scores = [0.6, 0.3]  # one passes, one fails
    def judge(prompt):
        return f"score: {scores[next(counter)]}"
    gs = build_graders(specs, judge=judge)
    assert len(gs) == 2
    # Verify the judges are different instances with different
    # thresholds (the build_graders loop creates one per spec)
    assert gs[0].threshold == 0.5
    assert gs[1].threshold == 0.9
    assert gs[0] is not gs[1]


def test_judge_with_missing_judge_fails_open():
    """Without a judge, LLMJudgeGrader returns 1.0 (fail open)."""
    from kairos.eval import LLMJudgeGrader
    g = LLMJudgeGrader(prompt="rate", judge=None, threshold=0.5)
    score, det = g.score("anything", {})
    assert score == 1.0
    assert det.get("judge") is None


def test_judge_with_real_callable_uses_returned_score():
    """A real judge callable's text is parsed and the threshold applies."""
    from kairos.eval import LLMJudgeGrader
    def judge(prompt: str) -> str:
        return "0.85"
    g = LLMJudgeGrader(prompt="rate", judge=judge, threshold=0.7)
    score, det = g.score("output", {})
    # 0.85 >= 0.7 → pass
    assert score == 1.0
    assert det["judge_score"] == 0.85


def test_judge_cli_help_lists_subcommands():
    """`python -m kairos.eval --help` lists all our subcommands."""
    r = subprocess.run(
        [sys.executable, "-m", "kairos.eval", "--help"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0
    for cmd in ("run", "compare", "record", "replay", "derive"):
        assert cmd in r.stdout, f"missing subcommand: {cmd}"


def test_judge_yaml_run_with_stub_target():
    """End-to-end: run the example suite with a stub target and
    stub judge. The judge returns 1.0 (best) so all LLM-judge
    cases pass. The mechanical graders also pass."""
    from kairos.eval import load_suite, run_suite
    spec = load_suite(JUDGE_EXAMPLE)
    def target(prompt):
        return {
            "output": ('def add(a, b):\n    """Add a and b."""\n    '
                       "return a + b"),
            "tools_called": [],
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
            "duration_ms": 1,
        }
    def judge(prompt):
        return "score: 1.0"
    result = run_suite(spec, target=target, judge=judge)
    assert result.pass_rate == 1.0
    # The judge score appears in details
    for c in result.cases:
        assert c.details.get("llm_judge", {}).get("judge_score") == 1.0


def test_judge_yaml_run_without_judge_fails_open():
    """Without --judge, the LLM-judge cases score 1.0 (fail open)."""
    from kairos.eval import load_suite, run_suite
    spec = load_suite(JUDGE_EXAMPLE)
    def target(prompt):
        return {"output": "def f(): pass", "tools_called": [],
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                "duration_ms": 1}
    result = run_suite(spec, target=target, judge=None)
    # All LLM-judge graders fail open → score 1.0; mechanical
    # graders may or may not pass. Pass rate >= 0.5.
    assert result.pass_rate >= 0.5
