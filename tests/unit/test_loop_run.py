"""Tests for run_loop termination gates and prompt-boundedness.

Each test wires up a LoopSession with stub Coder + Reviewer agents
that return canned responses, then asserts that run_loop exits at
the right gate with the right reason.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, List

import pytest

from kairos.core.message_bus import MessageBus
from kairos.loop import review_loop as rl

# ---------------------------------------------------------------- stub agents

class StubAgent:
    """Minimal agent stub. Returns canned strings from .run(); never calls
    an LLM. Tracks call count and last task so tests can introspect."""

    def __init__(self, responses: List[str]):
        self._responses = list(responses)
        self.calls = 0
        self.last_task = None
        self.last_plan_mode = None

    async def run(self, task, plan_mode: bool = False) -> str:
        self.calls += 1
        self.last_task = task
        self.last_plan_mode = plan_mode
        if not self._responses:
            return "(no more responses)"
        return self._responses.pop(0)

def _verdict(approve: bool = False, score: int = 0,
             issues: List[dict] | None = None,
             summary: str = "x",
             failure_mode: str | None = None) -> str:
    """Build a Reviewer verdict string in the JSON shape _parse_review_verdict accepts."""
    obj = {"approve": approve, "score": score,
           "issues": issues or [], "summary": summary}
    if failure_mode:
        obj["_failure_mode"] = failure_mode
    return json.dumps(obj)

def _issue(file: str = "x.py", line: int = 1, severity: str = "MAJOR",
           description: str = "bug") -> dict:
    return {"category": "correctness", "severity": severity,
            "file": file, "line": line, "description": description,
            "fix_instruction": "fix"}

def _session(coder_responses: List[str], reviewer_responses: List[str],
             project_id: str = "p1",
             work_dir: str | None = None) -> rl.LoopSession:
    """Build a LoopSession wired to stub agents and a real MessageBus.

    `work_dir` defaults to a fresh temp directory on purpose: the loop's
    checkpoint commits the workspace, and the default workspace is the process
    CWD (this checkout) — which would commit the whole repository on every round.
    """
    import tempfile
    from pathlib import Path as _Path

    class StubProject:
        def __init__(self, pid):
            self.id = pid
            self.requirements = "build a thing"

    workspace = _Path(work_dir) if work_dir else _Path(
        tempfile.mkdtemp(prefix="kairos-loop-test-"))
    workspace.mkdir(parents=True, exist_ok=True)
    session = rl.LoopSession(
        project=StubProject(project_id),
        message_bus=MessageBus(),
        coder=StubAgent(coder_responses),
        reviewer=StubAgent(reviewer_responses),
        persistence=None,
    )
    # LoopSession keeps its own reference to the working tree; set whichever
    # attributes this version uses so no code path falls back to the CWD.
    for attr in ("work_dir", "workspace", "workdir"):
        if hasattr(session, attr):
            try:
                setattr(session, attr, str(workspace))
            except Exception:
                pass
    return session

# ---------------------------------------------------------------- constant tests

def test_approve_threshold_is_75():
    """Lowered from 85 to 75 — the old value made well-meaning reviewers
    never approve and burned all 50 rounds."""
    assert rl.APPROVE_SCORE_THRESHOLD == 75

def test_cost_token_cap_defined():
    assert rl.COST_TOKEN_CAP > 0
    assert rl.COST_TOKEN_CAP < 10_000_000

def test_cost_time_cap_defined():
    assert rl.COST_TIME_CAP_S > 0

def test_infra_failure_limit_defined():
    assert rl.INFRA_FAILURE_LIMIT >= 3

def test_stagnation_window_defined():
    assert rl.STAGNATION_WINDOW >= 2
    assert rl.STAGNATION_TOLERANCE >= 0

# ---------------------------------------------------------------- field tests

def test_session_has_new_fields():
    s = _session([], [])
    assert hasattr(s, "original_requirement")
    assert hasattr(s, "plan_completed")
    assert hasattr(s, "infra_failure_streak")
    assert hasattr(s, "score_window")
    assert hasattr(s, "total_tokens_used")
    assert hasattr(s, "round_tokens")
    # Defaults
    assert s.original_requirement == ""
    assert s.plan_completed is False
    assert s.infra_failure_streak == 0
    assert s.score_window == []
    assert s.total_tokens_used == 0
    assert s.round_tokens == 0

# ---------------------------------------------------------------- signature tests

def test_issues_signature_includes_severity():
    """Old signature used (file, line, desc) — a Coder that 'fixed' a
    CRITICAL by rephrasing it as MAJOR counted as 'different issue' and
    never tripped the no-progress gate. Adding severity closes that."""
    a = [_issue(file="x.py", line=10, severity="CRITICAL", description="d")]
    b = [_issue(file="x.py", line=10, severity="MAJOR", description="d")]
    assert rl._issues_signature(a) != rl._issues_signature(b)

def test_issues_signature_unchanged_when_severity_same():
    a = [_issue(file="x.py", line=10, severity="MAJOR", description="d1")]
    b = [_issue(file="x.py", line=10, severity="MAJOR", description="d2")]
    # Different description \u2192 still 'same issue set' under the old signature;
    # new signature also matches here (we only add severity, not full
    # description hashing). Documented behavior \u2014 keeps the no-progress
    # gate honest without overcounting trivial rewording.
    assert rl._issues_signature(a) == rl._issues_signature(b)

# ---------------------------------------------------------------- prompt-boundedness tests

def test_build_next_prompt_does_not_compound():
    """The original bug: each round re-injected prev_req[:2000] into the
    next prompt, growing linearly. The fix uses session.original_requirement
    \u2014 so 5 successive rounds must produce 5 prompts of similar size."""
    session = _session([], [])
    session.original_requirement = "fix the login bug" * 10  # ~180 chars
    review = {"approve": False, "score": 60,
              "summary": "missed one thing",
              "issues": [_issue(description="still buggy")]}

    prompts = [rl._build_next_prompt(session, review) for _ in range(5)]
    sizes = [len(p) for p in prompts]
    # Each prompt should be the same length (no compounding).
    assert max(sizes) - min(sizes) == 0, (
        f"prompt sizes diverged across rounds: {sizes}"
    )
    # And the user's requirement must appear verbatim every time.
    assert all(session.original_requirement in p for p in prompts)

def test_build_next_prompt_uses_original_not_callers_requirement():
    """If a caller mistakenly passes a mutated 'requirement' string, the
    helper must still use session.original_requirement."""
    session = _session([], [])
    session.original_requirement = "ORIGINAL SPEC"
    review = {"approve": False, "score": 60, "summary": "x", "issues": []}
    prompt = rl._build_next_prompt(session, review)
    assert "ORIGINAL SPEC" in prompt

# ---------------------------------------------------------------- end-to-end gate tests

@pytest.mark.asyncio
async def test_approve_gate_stops_loop_after_one_round():
    """approve=true, score=75, no critical \u2192 loop exits after R1."""
    coder = StubAgent(["initial implementation"])
    reviewer = StubAgent([_verdict(approve=True, score=75, summary="lgtm")])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    await rl.run_loop(session, "do the thing")
    # 1 coder call + 1 reviewer call \u2014 loop exited at approval gate
    assert coder.calls == 1
    assert reviewer.calls == 1
    assert session.last_approve is True
    assert session.last_score == 75

@pytest.mark.asyncio
async def test_approve_below_threshold_does_not_stop():
    """score=74 < threshold(75) \u2192 must NOT approve, even if approve=true."""
    coder = StubAgent(["x", "x", "x"])
    reviewer = StubAgent([
        _verdict(approve=True, score=74, summary="just below"),
        _verdict(approve=True, score=80, summary="lgtm"),
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    await rl.run_loop(session, "x")
    assert reviewer.calls == 2  # R1 rejected by threshold, R2 approved

@pytest.mark.asyncio
async def test_no_progress_gate_stops_after_5_repeats():
    """Same 5 issues 5 rounds in a row \u2192 loop stops at NO_PROGRESS_LIMIT."""
    same_issues = [_issue(file=f"f{i}.py", description=f"bug {i}") for i in range(5)]
    coder = StubAgent(["x"] * 10)
    reviewer = StubAgent([
        _verdict(approve=False, score=50, issues=same_issues, summary="same again")
        for _ in range(10)
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    await rl.run_loop(session, "x")
    assert reviewer.calls == rl.NO_PROGRESS_LIMIT  # exact stop at limit
    assert session.no_progress_count == rl.NO_PROGRESS_LIMIT

@pytest.mark.asyncio
async def test_infra_failure_streak_stops():
    """5 consecutive parse_fail rounds \u2192 infra_streak gate fires
    (no_progress never trips because it resets on infra failures)."""
    coder = StubAgent(["x"] * 10)
    reviewer = StubAgent([
        _verdict(approve=False, score=0, issues=[], summary="parse broke",
                 failure_mode="parse_fail")
        for _ in range(10)
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    await rl.run_loop(session, "x")
    assert reviewer.calls == rl.INFRA_FAILURE_LIMIT
    # Critical: the no_progress counter MUST stay at 0 (resets each
    # infra round) \u2014 that's the bug we're guarding against.
    assert session.no_progress_count == 0
    assert session.infra_failure_streak == rl.INFRA_FAILURE_LIMIT

@pytest.mark.asyncio
async def test_score_stagnation_stops():
    """3 consecutive scores within tolerance, all below threshold \u2192 stops."""
    coder = StubAgent(["x"] * 10)
    reviewer = StubAgent([
        _verdict(approve=False, score=60, issues=[_issue(description=f"v{i}")],
                 summary=f"r{i}")
        for i in range(10)
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    await rl.run_loop(session, "x")
    assert reviewer.calls == rl.STAGNATION_WINDOW
    assert session.score_window[-1] == 60

@pytest.mark.asyncio
async def test_cost_token_cap_stops(monkeypatch):
    """Token cap fires before the 50-round hard cap."""
    # Force a tiny cap so we don't need to fill the agent with 500k tokens.
    from kairos.loop import gates as gates_mod
    monkeypatch.setattr(gates_mod, "COST_TOKEN_CAP", 100)
    monkeypatch.setattr(rl, "COST_TOKEN_CAP", 100)
    monkeypatch.setattr("kairos.loop.loop_runner.COST_TOKEN_CAP", 100)
    coder = StubAgent(["x" * 200] * 10)  # each \u224850 tokens
    reviewer = StubAgent([
        _verdict(approve=False, score=10, issues=[_issue(description=f"r{i}")],
                 summary="bad")
        for i in range(10)
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x" * 400})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    await rl.run_loop(session, "x" * 400)
    # Should stop well before the 50-round safety cap.
    assert reviewer.calls < rl.LOOP_SAFETY_CAP

@pytest.mark.asyncio
async def test_safety_cap_stops_at_50():
    """With every other gate bypassed, the 50-round hard cap still fires."""
    coder = StubAgent(["x"] * 60)
    reviewer = StubAgent([
        _verdict(approve=False, score=20,
                 issues=[_issue(description=f"r{i}", file=f"f{i}.py")],
                 summary=f"r{i}")
        for i in range(60)
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    await rl.run_loop(session, "x")
    assert reviewer.calls == rl.LOOP_SAFETY_CAP

@pytest.mark.asyncio
async def test_user_stop_exits_immediately():
    """If user_stopped is set before run_loop starts, the loop never enters."""
    coder = StubAgent(["x"] * 5)
    reviewer = StubAgent([_verdict(approve=True, score=90, summary="lgtm")] * 5)
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    session.user_stopped = True
    await rl.run_loop(session, "x")
    assert coder.calls == 0
    assert reviewer.calls == 0

@pytest.mark.asyncio
async def test_plan_rejected_exits_early():
    """plan_decision='reject' before loop starts \u2192 loop exits after
    the plan-mode branch without running any execution rounds."""
    coder = StubAgent(["my plan"])
    reviewer = StubAgent([])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    session.plan_decision = "reject"  # pre-rejected
    await rl.run_loop(session, "x")
    # Coder ran exactly once (the plan), reviewer never.
    assert coder.calls == 1
    assert reviewer.calls == 0

@pytest.mark.asyncio
async def test_round_counter_monotonic_no_reset():
    """With plan_decision pre-set (CLI path), the loop does NOT enter
    the plan-mode branch and runs straight to coder+reviewer. The
    round counter must increment monotonically (no `session.round = 0`
    reset on plan approval)."""
    coder = StubAgent(["exec1", "exec2", "exec3"])
    reviewer = StubAgent([
        _verdict(approve=False, score=50, issues=[_issue(description="bug")],
                 summary="r1"),
        _verdict(approve=False, score=55, issues=[_issue(description="bug")],
                 summary="r2"),
        _verdict(approve=True, score=90, summary="lgtm"),
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    session.plan_decision = "approve"  # skip plan-mode
    await rl.run_loop(session, "x")
    # R1: score 50 (rejected), R2: score 55 (rejected), R3: score 90 (approved).
    assert session.round == 3
    assert session.last_approve is True

@pytest.mark.asyncio
async def test_original_requirement_snapshotted_on_first_round():
    """After run_loop completes, session.original_requirement must equal
    the original input \u2014 even if internal 'requirement' mutated."""
    coder = StubAgent(["x"] * 5)
    reviewer = StubAgent([
        _verdict(approve=False, score=40, issues=[_issue(description="bug")],
                 summary="bad"),
        _verdict(approve=True, score=90, summary="lgtm"),
    ])
    session = rl.LoopSession(
        project=type("P", (), {"id": "p1", "requirements": "x"})(),
        message_bus=MessageBus(),
        coder=coder,
        reviewer=reviewer,
        persistence=None,
    )
    original = "FIX THE LOGIN BUG"
    await rl.run_loop(session, original)
    assert session.original_requirement == original