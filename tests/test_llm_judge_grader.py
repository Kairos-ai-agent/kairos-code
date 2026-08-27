"""Tests for the LLM-judge grader in kairos.eval.

We stub the judge callable so tests don't hit a real LLM. The
production path uses ``kairos.eval.make_litellm_judge`` which
wraps litellm; the wiring is tested separately.
"""
from __future__ import annotations

import pytest

from kairos.eval import LLMJudgeGrader, _default_parse_score, build_graders, run_suite


# ---------------------------------------------------------------------------
# Default score parser
# ---------------------------------------------------------------------------


def test_parse_score_explicit_pattern():
    assert _default_parse_score("score: 0.85") == 0.85
    assert _default_parse_score("Score: 0.7") == 0.7
    assert _default_parse_score("score=0.5") == 0.5


def test_parse_score_fallback_to_last_number():
    """No "score:" pattern → use the last number on the last line."""
    assert _default_parse_score("Some explanation.\n0.9") == 0.9
    assert _default_parse_score("Reasoning here.\nFinal: 0.42") == 0.42


def test_parse_score_percentage_normalized():
    """A value > 1 is treated as a percentage (1-100 → 0-1)."""
    assert _default_parse_score("score: 85") == 0.85
    assert _default_parse_score("0.95\nfoo") == 0.95


def test_parse_score_empty_or_garbage():
    assert _default_parse_score("") == 0.0
    assert _default_parse_score("no numbers here\nat all") == 0.0


def test_parse_score_clamped():
    """Values > 1 and < 0 are clamped after normalization."""
    assert _default_parse_score("score: 2.0") == 1.0  # 200% → clamped
    assert _default_parse_score("score: 1.5") == 1.0  # already > 1, clamped


# ---------------------------------------------------------------------------
# LLMJudgeGrader
# ---------------------------------------------------------------------------


def test_judge_no_callable_fails_open():
    """No judge configured → return 1.0 (grader is a no-op)."""
    g = LLMJudgeGrader(prompt="rate it", judge=None)
    score, det = g.score("anything", {})
    assert score == 1.0
    assert det.get("note") == "no judge configured"


def test_judge_passes_when_score_meets_threshold():
    def judge(prompt: str) -> str:
        return "The output is great. score: 0.9"
    g = LLMJudgeGrader(prompt="rate", judge=judge, threshold=0.7)
    score, det = g.score("agent output", {})
    assert score == 1.0
    assert det["judge_score"] == 0.9


def test_judge_fails_when_score_below_threshold():
    def judge(prompt: str) -> str:
        return "Boring. score: 0.3"
    g = LLMJudgeGrader(prompt="rate", judge=judge, threshold=0.7)
    score, det = g.score("agent output", {})
    assert score == 0.0
    assert det["judge_score"] == 0.3


def test_judge_handles_exception():
    """A judge that throws → score 0.0 with judge_error in details."""
    def bad_judge(prompt: str) -> str:
        raise RuntimeError("API down")
    g = LLMJudgeGrader(prompt="rate", judge=bad_judge)
    score, det = g.score("agent output", {})
    assert score == 0.0
    assert "API down" in det.get("judge_error", "")


def test_judge_prompt_includes_agent_output():
    """The agent output is passed to the judge in the full prompt."""
    captured = []

    def judge(prompt: str) -> str:
        captured.append(prompt)
        return "score: 0.5"
    g = LLMJudgeGrader(prompt="My rubric here", judge=judge)
    g.score("AGENT_OUTPUT_HERE", {})
    assert "My rubric here" in captured[0]
    assert "AGENT_OUTPUT_HERE" in captured[0]
    # Output is truncated at 4000 chars (so giant outputs don't blow
    # the judge's context window).
    assert len(captured[0]) < 5000


# ---------------------------------------------------------------------------
# build_graders integration
# ---------------------------------------------------------------------------


def test_build_graders_with_llm_judge_passes_judge():
    judge = lambda p: "score: 0.8"
    gs = build_graders(
        [{"llm_judge": {"prompt": "rate", "threshold": 0.7}}],
        judge=judge,
    )
    assert len(gs) == 1
    assert isinstance(gs[0], LLMJudgeGrader)
    score, _ = gs[0].score("output", {})
    assert score == 1.0  # 0.8 ≥ 0.7


def test_build_graders_without_judge_fails_open():
    """llm_judge spec without a judge callable falls back to 1.0."""
    gs = build_graders([{"llm_judge": {"prompt": "rate"}}])
    assert len(gs) == 1
    score, det = gs[0].score("anything", {})
    assert score == 1.0


def test_build_graders_unknown_judge_spec_warns(caplog):
    build_graders([{"llm_judge": "not a dict"}])
    # The warning was logged but no grader was created
    assert any("llm_judge spec must be a dict" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# run_suite with LLM judge
# ---------------------------------------------------------------------------


def test_run_suite_uses_judge_for_llm_judge_case():
    """An llm_judge case in a YAML suite uses the judge callable."""
    spec = {
        "name": "demo",
        "cases": [
            {"name": "c1", "input": "p1",
             "graders": [
                 {"contains": ["hello"]},
                 {"llm_judge": {"prompt": "rate", "threshold": 0.7}},
             ]},
        ],
    }

    def target(prompt: str):
        return {"output": "hello world", "tools_called": [],
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                "duration_ms": 1}

    # Judge returns 0.9 — both graders pass
    res = run_suite(spec, target=target, judge=lambda p: "score: 0.9")
    assert res.cases[0].passed is True
    assert res.cases[0].score == 1.0
    # Details include the LLM judge score
    det = res.cases[0].details.get("llm_judge", {})
    assert det.get("judge_score") == 0.9

    # Judge returns 0.3 — llm_judge fails, contains passes → score 0.5
    # (default threshold is 0.5 → exactly 0.5 passes by >=).
    res = run_suite(spec, target=target, judge=lambda p: "score: 0.3")
    assert res.cases[0].score == 0.5  # average of 1.0 and 0.0
    # With a per-case threshold of 0.7 (stricter than default 0.5),
    # the 0.5 score fails.
    spec_strict = {**spec,
                   "cases": [{**spec["cases"][0],
                              "threshold": 0.7,
                              "graders": [{"contains": ["hello"]},
                                          {"llm_judge": {"prompt": "rate",
                                                          "threshold": 0.5}}]}]}
    res2 = run_suite(spec_strict, target=target, judge=lambda p: "score: 0.3")
    assert res2.cases[0].passed is False  # 0.5 < 0.7 case threshold
