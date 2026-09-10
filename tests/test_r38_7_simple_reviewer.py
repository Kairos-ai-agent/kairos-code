"""R38.7 — the Reviewer is a bug checker, nothing else.

User feedback: "reviewer agent 太复杂，改成简单的只检查代码是否存在bug即可".

The Reviewer used to be a four-dimension grader (correctness 40 / design 25 /
quality 20 / security 15) with a weighted score, mandatory test evidence,
optional ask-human and confidence calibration. It now answers one question —
"are there bugs?" — and the loop maps that onto its approve/score gates.

These tests pin down:
- the prompt is bug-only (no rubric, no scoring, no test-evidence mandate)
- the simple verdict shape normalizes correctly (no bugs -> approve)
- the test-evidence calibration no longer blocks the simple verdict
- legacy rubric verdicts still parse (old sessions / replays)
- the next-round Coder prompt talks about bugs, not scores
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kairos.agents.roles.reviewer import SYSTEM_PROMPT  # noqa: E402
from kairos.loop.loop_runner import _calibrate_review  # noqa: E402
from kairos.loop.prompts import (  # noqa: E402
    build_next_prompt,
    build_reviewer_description,
)
from kairos.loop.reviewers import parse_review_verdict  # noqa: E402


# ---------------------------------------------------------------------------
# The prompt itself
# ---------------------------------------------------------------------------


def test_prompt_is_bug_only():
    assert '"has_bugs"' in SYSTEM_PROMPT
    assert '"bugs"' in SYSTEM_PROMPT
    assert "What counts as a bug" in SYSTEM_PROMPT


@pytest.mark.parametrize("gone", [
    "Scoring rubric",
    "Correctness (40%)",
    "tests_evidence",
    "Test evidence (required)",
    "ask_human",
    "Confidence calibration",
    "CRITICAL",
    "SUGGESTION",
])
def test_prompt_dropped_the_grading_machinery(gone):
    assert gone not in SYSTEM_PROMPT


def test_reviewer_task_description_asks_for_bugs_only():
    class _P:
        id = "p1"
        requirements = "写一个 hello.py"

    class _S:
        project = _P()
        history = []
        review_focus = []

    desc = build_reviewer_description(_S(), "coder wrote hello.py", 1)
    assert "has_bugs" in desc
    assert "ONLY real bugs" in desc
    # the old wording pushed the Reviewer into running the test suite
    assert "run relevant tests" not in desc


# ---------------------------------------------------------------------------
# Verdict normalization
# ---------------------------------------------------------------------------


def test_no_bugs_approves():
    verdict = parse_review_verdict(
        '{"has_bugs": false, "bugs": [], "summary": "looks fine"}')
    assert verdict["approve"] is True
    assert verdict["score"] == 100
    assert verdict["issues"] == []
    assert verdict["summary"] == "looks fine"
    assert verdict["_simple_bug_review"] is True


def test_bugs_reject_and_keep_the_fix_instruction():
    verdict = parse_review_verdict(
        '{"has_bugs": true, "bugs": [{"file": "a.py", "line": 3, '
        '"description": "off-by-one on the last row", "fix": "use <="}], '
        '"summary": "1 bug"}')
    assert verdict["approve"] is False
    assert verdict["score"] == 80          # below the 85 approval bar
    assert len(verdict["issues"]) == 1
    issue = verdict["issues"][0]
    assert issue["file"] == "a.py"
    assert issue["line"] == 3
    assert issue["severity"] == "BUG"
    assert issue["fix_instruction"] == "use <="
    assert verdict["_simple_bug_review"] is True


def test_has_bugs_true_with_empty_list_still_rejects():
    """A Reviewer that refuses to approve must not be overruled."""
    verdict = parse_review_verdict('{"has_bugs": true, "bugs": []}')
    assert verdict["approve"] is False
    assert len(verdict["issues"]) == 1


def test_fenced_and_chatty_json_still_parses():
    raw = ('Sure! Here is my verdict:\n```json\n'
           '{"has_bugs": false, "bugs": [], "summary": "no bugs"}\n```')
    assert parse_review_verdict(raw)["approve"] is True


def test_legacy_rubric_verdict_still_parses():
    """Old sessions / replays keep working (back-compat path)."""
    verdict = parse_review_verdict(
        '{"approve": true, "score": 91, "issues": [], "summary": "legacy"}')
    assert verdict["approve"] is True
    assert verdict["score"] == 91
    assert "_simple_bug_review" not in verdict


def test_unparseable_output_is_an_infra_failure():
    verdict = parse_review_verdict("I could not review this.")
    assert verdict["approve"] is False
    assert verdict["_failure_mode"] == "parse_fail"


# ---------------------------------------------------------------------------
# The loop must stay able to finish
# ---------------------------------------------------------------------------


def test_calibration_does_not_block_the_simple_verdict():
    """require_test_evidence=True used to clamp every verdict without a test
    run below the approval bar — with a Reviewer that does not run tests that
    would mean the loop never ends."""
    verdict = parse_review_verdict('{"has_bugs": false, "bugs": []}')
    calibrated = _calibrate_review(verdict, True)
    assert calibrated["approve"] is True
    assert calibrated["score"] == 100
    assert "_calibration" not in calibrated


def test_calibration_still_guards_legacy_verdicts():
    legacy = {"approve": True, "score": 95, "issues": [], "summary": "lgtm"}
    calibrated = _calibrate_review(legacy, True)
    assert calibrated["approve"] is False
    assert calibrated["score"] <= 84
    assert calibrated["_calibration"]["missing_test_evidence"] is True


# ---------------------------------------------------------------------------
# The Coder is told to fix bugs, not to raise a score
# ---------------------------------------------------------------------------


def test_next_round_prompt_is_bug_framed():
    class _S:
        round = 2
        original_requirement = "写一个 hello.py"
        history = []

    review = parse_review_verdict(
        '{"has_bugs": true, "bugs": [{"file": "a.py", "line": 3, '
        '"description": "off-by-one", "fix": "use <="}], "summary": "1 bug"}')
    prompt = build_next_prompt(_S(), review)
    assert "The Reviewer found 1 bug(s)" in prompt
    assert "Bugs to fix this round:" in prompt
    assert "off-by-one" in prompt
    assert "Do not refactor, rename, restyle" in prompt
    assert "写一个 hello.py" in prompt
