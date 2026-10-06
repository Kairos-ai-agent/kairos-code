"""Reviewer/precheck feedback must reach the NEXT round's Coder.

Before this fix ``loop_runner`` imported ``build_next_prompt`` but never
called it: every round handed the Coder the identical ``requirement``
string, so the Reviewer's issues and the precheck failures could not
change the Coder's input and the loop repeated itself instead of
converging.

These tests pin the contract:

* round 1 keeps the raw requirement (no feedback yet);
* round > 1 gets ``build_next_prompt`` output (issues + precheck hint);
* best-of-N attempts get the same feedback description;
* missing / empty prior review falls back to the raw requirement and
  never raises.
"""
from __future__ import annotations

import json
from typing import Any, List

import pytest

from kairos.core.message_bus import MessageBus
from kairos.loop import loop_runner as lr
from kairos.loop import review_loop as rl


# ---------------------------------------------------------------- stubs

class RecordingAgent:
    """Agent stub that records every AgentTask it is handed."""

    def __init__(self, responses: List[str], record: bool = True):
        self._responses = list(responses)
        self.calls = 0
        self.tasks: List[Any] = []
        self._record = record

    async def run(self, task, plan_mode: bool = False) -> str:
        self.calls += 1
        if self._record:
            self.tasks.append(task)
        if not self._responses:
            return "(no more responses)"
        return self._responses.pop(0)

    def fork(self):
        # best-of-N forks the Coder; a lightweight self-return is enough
        # because ``_run_coder_round`` is patched in that test.
        return self


def _verdict(approve: bool = False, score: int = 0,
             issues: List[dict] | None = None, summary: str = "x") -> str:
    return json.dumps({"approve": approve, "score": score,
                       "issues": issues or [], "summary": summary})


def _issue(description: str, file: str = "x.py", line: int = 1) -> dict:
    return {"category": "correctness", "severity": "MAJOR", "file": file,
            "line": line, "description": description,
            "fix_instruction": "fix it"}


def _make_session(tmp_path, coder, reviewer, best_of_n: int = 1):
    class StubProject:
        def __init__(self):
            self.id = "p1"
            self.requirements = "x"
            self.work_dir = str(tmp_path)
            self.workspace = str(tmp_path)

    session = rl.LoopSession(
        project=StubProject(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    session.plan_decision = "approve"  # skip the plan-mode branch
    session.best_of_n = best_of_n
    return session


@pytest.fixture
def fixed_precheck(monkeypatch):
    """Deterministic precheck hint so the assertion is not host-dependent."""
    async def fake_precheck(session, workspace, round_no, bus):
        return "PRECHECK_FAIL_ABC", []
    monkeypatch.setattr(lr, "_run_precheck", fake_precheck)
    return "PRECHECK_FAIL_ABC"


# ---------------------------------------------------------------- (a) round 2 feedback

@pytest.mark.asyncio
async def test_round2_description_carries_issues_and_precheck_hint(
        tmp_path, fixed_precheck):
    """Round 2's Coder description must contain round 1's issues + precheck
    hint — i.e. the Reviewer's verdict actually reaches the next Coder."""
    requirement = "BUILD THE UNIQUE REQUIREMENT"
    coder = RecordingAgent(["coder-r1", "coder-r2", "coder-r3"])
    reviewer = RecordingAgent([
        _verdict(False, 50, [_issue("BUG_ISSUE_47")], "round1 summary"),
        _verdict(False, 55, [_issue("BUG_ISSUE_48")], "round2 summary"),
        _verdict(True, 90, summary="lgtm"),
    ])
    session = _make_session(tmp_path, coder, reviewer)

    await lr.run_loop(session, requirement)

    assert len(coder.tasks) == 3, "expected 3 coder rounds"
    round2 = coder.tasks[1].description
    round3 = coder.tasks[2].description

    # The round-1 issue and the precheck failure both ride into round 2.
    assert "BUG_ISSUE_47" in round2, round2
    assert fixed_precheck in round2, round2
    assert "Bugs to fix this round" in round2
    # Round 3 sees round 2's issue, not round 1's stale one.
    assert "BUG_ISSUE_48" in round3, round3
    assert "BUG_ISSUE_47" not in round3
    assert session.last_coder_prompt_source == "review_feedback"


# ---------------------------------------------------------------- (b) round 1 unchanged

@pytest.mark.asyncio
async def test_round1_description_is_raw_requirement(tmp_path, fixed_precheck):
    """The first round must still send the untouched requirement — no
    feedback exists yet, so any mutation would be a regression."""
    requirement = "BUILD THE UNIQUE REQUIREMENT"
    coder = RecordingAgent(["coder-r1"])
    reviewer = RecordingAgent([_verdict(True, 90, summary="lgtm")])
    session = _make_session(tmp_path, coder, reviewer)

    await lr.run_loop(session, requirement)

    assert len(coder.tasks) == 1
    assert coder.tasks[0].description == requirement
    assert "Bugs to fix this round" not in coder.tasks[0].description
    assert session.last_coder_prompt_source == "requirement"


# ---------------------------------------------------------------- (c) fallbacks

def test_round2_without_history_falls_back_to_requirement():
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=RecordingAgent([]),
        reviewer=RecordingAgent([]),
        persistence=None,
    )
    desc, source = lr._effective_coder_description(session, "RAW_REQ", 2)
    assert desc == "RAW_REQ"
    assert source == "requirement_no_review"


def test_round1_never_calls_build_next_prompt():
    """round_no <= 1 short-circuits before any review lookup."""
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=RecordingAgent([]),
        reviewer=RecordingAgent([]),
        persistence=None,
    )
    # Even a poisoned history must not matter on round 1.
    session.history = [{"round": 1, "review": {"issues": [{"description": "nope"}]}}]
    desc, source = lr._effective_coder_description(session, "RAW_REQ", 1)
    assert desc == "RAW_REQ"
    assert source == "requirement"
    assert "nope" not in desc


def test_empty_or_missing_review_falls_back(tmp_path):
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=RecordingAgent([]),
        reviewer=RecordingAgent([]),
        persistence=None,
    )
    for bad_history in (
        [{"round": 1}],                          # no review key
        [{"round": 1, "review": {}}],            # empty review dict
        [{"round": 1, "review": None}],          # None review
        ["not-a-dict"],                          # malformed entry
        [],                                      # no history at all
    ):
        session.history = bad_history
        desc, source = lr._effective_coder_description(session, "RAW_REQ", 2)
        assert desc == "RAW_REQ", bad_history
        assert source == "requirement_no_review", bad_history


def test_build_failure_falls_back_without_raising(tmp_path, monkeypatch):
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=RecordingAgent([]),
        reviewer=RecordingAgent([]),
        persistence=None,
    )
    session.history = [{"round": 1, "review": {"issues": [_issue("b")]}}]

    def boom(*a, **k):
        raise RuntimeError("prompt builder exploded")

    monkeypatch.setattr(lr, "build_next_prompt", boom)
    desc, source = lr._effective_coder_description(session, "RAW_REQ", 2)
    assert desc == "RAW_REQ"
    assert source == "requirement_prompt_failed"


# ---------------------------------------------------------------- (d) best-of-N path

@pytest.mark.asyncio
async def test_best_of_n_round2_attempts_receive_feedback(
        tmp_path, fixed_precheck, monkeypatch):
    """best-of-N spawns N Coder attempts on the same round; each must get
    the feedback description, not the raw requirement."""
    requirement = "BUILD THE UNIQUE REQUIREMENT"
    recorded: List[tuple] = []

    async def fake_run_coder_round(session, req, round_no,
                                   plan_mode=False, coder=None):
        recorded.append((round_no, req))
        return "attempt-output CONFIDENCE: 50"

    monkeypatch.setattr(lr, "_run_coder_round", fake_run_coder_round)

    coder = RecordingAgent([], record=False)
    reviewer = RecordingAgent([
        _verdict(False, 50, [_issue("BUG_ISSUE_47")], "round1 summary"),
        _verdict(True, 90, summary="lgtm"),
    ])
    session = _make_session(tmp_path, coder, reviewer, best_of_n=2)

    await lr.run_loop(session, requirement)

    round1 = [req for rnd, req in recorded if rnd == 1]
    round2 = [req for rnd, req in recorded if rnd == 2]
    assert round1 and all(req == requirement for req in round1)
    assert len(round2) == 2, f"expected 2 best-of-N attempts, got {round2}"
    assert all("BUG_ISSUE_47" in req for req in round2), round2
    assert all(fixed_precheck in req for req in round2), round2
    assert all("Bugs to fix this round" in req for req in round2), round2
