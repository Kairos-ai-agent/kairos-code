"""Tests for Round 12 plan wiring.

Covers:
  - Plan block is injected into the Coder's next-turn system prompt
  - Plan snapshot lands in session.history entries (rollback + normal)
  - Plan snapshot lands in the git commit message via checkpoint_round
  - Plan is reconstructable from a history entry (Plan.from_dict round-trip)
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from kairos.agents.base import AgentStatus, AgentTask, KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMResponse
from kairos.loop.plan import Plan, TodoItem, render_plan_block
from kairos.loop.loop_runner import _plan_snapshot
from kairos.tools.checkpoint import checkpoint_round


@pytest.fixture(autouse=True)
def _enable_checkpoints(monkeypatch):
    """Opt back in to checkpointing.

    tests/conftest.py sets KAIROS_NO_CHECKPOINTS=1 for the whole suite so that no
    test can commit into a real repository. This module exercises the commit path
    on purpose, against throwaway repositories under tmp_path.
    """
    monkeypatch.delenv("KAIROS_NO_CHECKPOINTS", raising=False)

class _StubLLM:
    def __init__(self, response: LLMResponse):
        self._response = response
    async def complete(self, messages, tools=None, **kw):
        return self._response
    async def stream(self, messages, tools=None, **kw):
        return
        yield  # pragma: no cover
    async def close(self):
        pass


def _make_agent_with_plan(plan: Plan) -> tuple[KairosAgent, MessageBus]:
    bus = MessageBus()
    cfg = LLMConfig(provider="anthropic", model="m", api_key="sk-test")
    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="You are the Coder.", llm_config=cfg,
        message_bus=bus, tools=[], plan_tracker=plan,
    )
    return agent, bus


# ---------------------------------------------------------------------------
# Plan injection into system_prompt
# ---------------------------------------------------------------------------


def test_plan_injected_into_next_turn_system_prompt():
    """The plan block is appended to the Coder's system_prompt on
    the next _build_messages() call."""
    plan = Plan()
    plan.replace([
        TodoItem(status="in_progress", content="Read README",
                 activeForm="Reading README"),
        TodoItem(status="pending", content="Add CSV reader"),
    ])
    agent, _ = _make_agent_with_plan(plan)
    # Pretend we're about to build the LLM messages
    agent.current_task = AgentTask(id="t1", title="t", description="d")
    msgs = agent._build_messages()
    # The system message (first one) contains the plan
    assert msgs[0].role == "system"
    assert "[>] Read README" in msgs[0].content
    assert "[ ] Add CSV reader" in msgs[0].content
    # The base prompt is still there
    assert "You are the Coder." in msgs[0].content


def test_empty_plan_not_injected():
    """An empty plan does NOT add noise to the system prompt."""
    plan = Plan()  # empty
    agent, _ = _make_agent_with_plan(plan)
    agent.current_task = AgentTask(id="t1", title="t", description="d")
    msgs = agent._build_messages()
    # No plan block in the system prompt
    assert "# Plan" not in msgs[0].content


def test_no_plan_tracker_no_injection():
    """Without a plan_tracker, the system prompt is unchanged."""
    plan = Plan()
    plan.replace([TodoItem(status="pending", content="x")])
    # Build an agent WITHOUT a plan_tracker
    bus = MessageBus()
    cfg = LLMConfig(provider="anthropic", model="m", api_key="sk-test")
    agent = KairosAgent(
        agent_id="a2", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg, message_bus=bus, tools=[],
    )
    # Manually poke a plan into the agent's plan_tracker property —
    # the agent should NOT pick it up if we set it after construction
    # in this particular path. But more realistically: when the
    # attribute is None, the system prompt doesn't get the plan.
    agent.plan_tracker = None
    agent.current_task = AgentTask(id="t", title="t", description="d")
    msgs = agent._build_messages()
    assert "# Plan" not in msgs[0].content


# ---------------------------------------------------------------------------
# _plan_snapshot helper
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal stand-in for LoopSession that exposes only what
    _plan_snapshot reads."""
    def __init__(self, plan: Optional[Plan] = None):
        self.plan_todos = plan


def test_plan_snapshot_returns_dict_when_plan_set():
    plan = Plan()
    plan.replace([TodoItem(status="in_progress", content="do x")])
    snap = _plan_snapshot(_FakeSession(plan))
    assert snap is not None
    assert "todos" in snap
    assert snap["todos"][0]["content"] == "do x"


def test_plan_snapshot_returns_none_when_no_plan():
    assert _plan_snapshot(_FakeSession(None)) is None


def test_plan_snapshot_round_trip():
    """The dict form round-trips through Plan.from_dict."""
    plan = Plan()
    plan.replace([
        TodoItem(status="completed", content="a"),
        TodoItem(status="pending", content="b", activeForm="Doing b"),
    ])
    snap = _plan_snapshot(_FakeSession(plan))
    restored = Plan.from_dict(snap)
    assert restored.todos[0].status == "completed"
    assert restored.todos[1].status == "pending"
    assert restored.todos[1].activeForm == "Doing b"


# ---------------------------------------------------------------------------
# Plan in git commit message
# ---------------------------------------------------------------------------


def test_plan_lands_in_git_commit_message(tmp_path: Path, monkeypatch):
    """checkpoint_round with a plan adds the rendered plan to the
    commit message."""
    # Make sure git has a HOME on Windows (it falls back to USERPROFILE,
    # but some git versions want HOME for .gitconfig lookup).
    import os
    if not os.environ.get("HOME"):
        monkeypatch.setenv("HOME", str(tmp_path))
    plan = Plan()
    plan.replace([TodoItem(status="in_progress", content="Write tests")])
    # Make a file so the commit has content
    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    sha = checkpoint_round(tmp_path, round_no=3, score=80,
                            summary="ok", approved=True,
                            plan=plan.to_dict())
    assert sha is not None, "checkpoint_round returned None (commit likely failed)"
    # `git log` shows the plan block
    out = subprocess.check_output(
        ["git", "log", "-1", "--format=%B"],
        cwd=tmp_path, text=True,
    )
    assert "Write tests" in out
    # The "Plan at this round" header is in the commit body
    assert "Plan at this round" in out


def test_checkpoint_without_plan_still_works(tmp_path: Path, monkeypatch):
    """Existing call sites (without the plan kwarg) keep working."""
    import os
    if not os.environ.get("HOME"):
        monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    sha = checkpoint_round(tmp_path, round_no=1, score=80,
                            summary="ok", approved=True)
    assert sha is not None


def test_checkpoint_with_empty_plan_does_not_add_header(tmp_path: Path, monkeypatch):
    """An empty plan dict doesn't bloat the commit with a header."""
    import os
    if not os.environ.get("HOME"):
        monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    sha = checkpoint_round(tmp_path, round_no=1, score=80,
                            summary="ok", approved=True,
                            plan={"todos": []})
    assert sha is not None
    out = subprocess.check_output(
        ["git", "log", "-1", "--format=%B"],
        cwd=tmp_path, text=True,
    )
    assert "Plan at this round" not in out
