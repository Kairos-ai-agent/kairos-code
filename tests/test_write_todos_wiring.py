"""Tests for the write_todos tool interception in kairos.agents.base.

The Coder agent should intercept ``write_todos`` tool calls and
apply them to the attached ``plan_tracker`` (a ``kairos.loop.plan.Plan``)
instead of dispatching them as real tool invocations.

We exercise the ``_handle_write_todos`` path directly on a
constructed ``KairosAgent`` with a stub LLM and message bus.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from kairos.agents.base import AgentStatus, AgentTask, KairosAgent
from kairos.core.message_bus import Message, MessageBus
from kairos.llm.base import LLMConfig, LLMResponse, ToolCall
from kairos.loop.plan import Plan, TodoItem
from kairos.tools.base import ToolResult


class _StubLLM:
    """Minimal LLM that returns a single response with the given
    tool_calls and a fixed content."""

    def __init__(self, response: LLMResponse):
        self._response = response
        self.calls: List[Dict[str, Any]] = []

    async def complete(self, messages, tools=None, **kw):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self._response

    async def stream(self, messages, tools=None, **kw):
        # Default: just return a single text chunk
        if False:
            yield ""
        return
        yield  # pragma: no cover (so it becomes an async generator)

    async def close(self):
        pass


def _make_agent(plan_tracker: Plan | None) -> tuple[KairosAgent, MessageBus]:
    bus = MessageBus()
    resp = LLMResponse(
        content="",
        model="m",
        tool_calls=[
            ToolCall(id="c1", name="write_todos", arguments={
                "todos": [
                    {"status": "in_progress", "content": "Read README",
                     "activeForm": "Reading README"},
                    {"status": "pending", "content": "Add CSV reader"},
                ],
            }),
        ],
    )
    llm = _StubLLM(resp)
    # Use a real registered provider name so __init__ succeeds
    # without needing a real API connection — we replace the
    # provider with the stub immediately after.
    cfg = LLMConfig(provider="anthropic", model="m", api_key="sk-test")
    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg, message_bus=bus,
        tools=[], plan_tracker=plan_tracker,
    )
    # Replace the LLM (init constructed one from cfg — bypass it)
    agent._llm = llm
    return agent, bus


# ---------------------------------------------------------------------------
# write_todos interception
# ---------------------------------------------------------------------------


def test_handle_write_todos_applies_to_plan():
    plan = Plan()
    plan.replace([TodoItem(status="pending", content="old")])
    agent, bus = _make_agent(plan)

    task = AgentTask(id="t1", title="t", description="d")
    result = asyncio.run(agent._handle_write_todos(
        {
            "todos": [
                {"status": "in_progress", "content": "Read README",
                 "activeForm": "Reading README"},
                {"status": "pending", "content": "Add CSV reader"},
            ],
        },
        task=task, turn=0,
    ))
    # The plan was updated
    assert result.success
    assert plan.todos[0].content == "Read README"
    assert plan.todos[0].status == "in_progress"
    assert plan.todos[1].content == "Add CSV reader"
    # The synthetic output mentions the plan
    assert "Plan updated" in result.output
    assert "Read README" in result.output
    assert "Add CSV reader" in result.output


def test_handle_write_todos_malformed_returns_error():
    """Garbage input should not crash — return a tool error."""
    plan = Plan()
    agent, _ = _make_agent(plan)
    task = AgentTask(id="t1", title="t", description="d")
    # Pass `todos` as a non-list (the apply_write_todos handler
    # already converts this to an error string).
    result = asyncio.run(agent._handle_write_todos(
        {"todos": "not a list"}, task=task, turn=0,
    ))
    assert not result.success
    assert "write_todos" in result.error.lower()


def test_handle_write_todos_publishes_plan_updated():
    """A successful write_todos publishes a plan.updated event."""
    plan = Plan()
    agent, bus = _make_agent(plan)
    task = AgentTask(id="t1", title="t", description="d")

    captured: List[Message] = []

    async def listen(msg):
        captured.append(msg)
    bus.add_listener(listen)

    asyncio.run(agent._handle_write_todos(
        {
            "todos": [
                {"status": "pending", "content": "New todo"},
            ],
        },
        task=task, turn=2,
    ))
    # Find the plan.updated event
    plan_msgs = [m for m in captured if m.topic == "plan.updated"]
    assert len(plan_msgs) == 1
    msg = plan_msgs[0]
    # Metadata includes the plan state and the turn
    assert "plan" in msg.metadata
    assert msg.metadata["plan"]["todos"][0]["content"] == "New todo"
    assert msg.metadata["turn"] == 3  # 0-based turn → 1-based turn + 1
    # The diff string is in the content
    assert "+ New todo" in msg.content


def test_handle_write_todos_no_diff_publishes():
    """Even no-op updates publish (so the UI can see the agent emitted)."""
    plan = Plan()
    plan.replace([TodoItem(status="pending", content="a")])
    agent, bus = _make_agent(plan)
    task = AgentTask(id="t1", title="t", description="d")

    captured: List[Message] = []
    bus.add_listener(lambda m: captured.append(m))

    asyncio.run(agent._handle_write_todos(
        {"todos": [{"status": "pending", "content": "a"}]},
        task=task, turn=0,
    ))
    plan_msgs = [m for m in captured if m.topic == "plan.updated"]
    assert len(plan_msgs) == 1
    assert "(no changes)" in plan_msgs[0].content


def test_handle_write_todos_without_plan_tracker_raises():
    """Without a plan_tracker attribute set, the intercept should
    NOT fire (the caller shouldn't even reach _handle_write_todos)."""
    bus = MessageBus()
    cfg = LLMConfig(provider="anthropic", model="m", api_key="sk-test")
    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg, message_bus=bus,
        tools=[],
        # No plan_tracker passed
    )
    # Verify the attribute is None (no plan_tracker)
    assert agent.plan_tracker is None
