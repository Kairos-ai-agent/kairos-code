"""Tests for subagent fork and plan_mode."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse


# ---------------------------------------------------------------------- Subagent

def test_subagent_tool_requires_task():
    from kairos.tools.subagent import SubagentTool
    tool = SubagentTool(allowed_root=".")
    tool.parent_agent = MagicMock()
    tool.project_id = "p1"
    res = asyncio.run(tool.execute(task=""))
    assert not res.success
    assert "task is required" in res.error


def test_subagent_tool_requires_wired_parent():
    from kairos.tools.subagent import SubagentTool
    tool = SubagentTool(allowed_root=".")
    tool.parent_agent = None
    res = asyncio.run(tool.execute(task="do something"))
    assert not res.success
    assert "not wired" in res.error


def test_subagent_tool_returns_child_result():
    """End-to-end: tool builds child Coder, runs it, returns its text.

    We patch BOTH import paths (kairos.agents.roles.coder.Coder and
    kairos.agents.roles.Coder) because subagent.py does
    `from kairos.agents.roles import Coder` — by the time that runs,
    the package-level binding is already populated and that's the one
    the import statement resolves to.
    """
    from kairos.tools.subagent import SubagentTool

    class FakeCoder:
        def __init__(self, agent_id, llm_config, message_bus, tools, **kw):
            self.agent_id = agent_id
            self._llm_config = llm_config
            self.message_bus = message_bus
            self.tools = tools
            self.MAX_TOOL_TURNS = 25

        async def run(self, task):
            return "child finished: 42"

    parent = MagicMock()
    parent._llm_config = LLMConfig(provider="openai", model="gpt-4o",
                                    api_key="sk-test")
    parent.message_bus = MagicMock()
    parent.message_bus.publish = AsyncMock()
    parent.tools = []

    # Patch BOTH binding locations so subagent's import sees FakeCoder.
    with patch("kairos.agents.roles.coder.Coder", FakeCoder), \
         patch("kairos.agents.roles.Coder", FakeCoder):
        tool = SubagentTool(allowed_root=".")
        tool.parent_agent = parent
        tool.project_id = "p1"
        res = asyncio.run(tool.execute(task="explore the repo"))

    assert res.success, f"unexpected failure: {res.error}"
    assert "child finished: 42" in res.output
    assert res.metadata["child_agent_id"].startswith("p1.sub_")


# ---------------------------------------------------------------------- Plan mode

@pytest.mark.asyncio
async def test_plan_mode_hides_tools_from_first_llm_call():
    """Plan mode: first turn the LLM sees no tool_schemas, so it must
    respond with prose (the plan) — no tool_calls possible."""
    from kairos.agents.base import AgentTask, KairosAgent
    captured = {}

    class FakeLLM:
        config = LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test")

        async def complete(self, messages, tools=None, **kw):
            captured["tools"] = tools
            return LLMResponse(
                content="PLAN: do X, Y, Z then stop.",
                model="m", tool_calls=None,
            )

        async def stream(self, messages, tools=None, **kw):
            captured["tools"] = tools
            yield "PLAN: do X, Y, Z then stop."

        async def close(self):
            pass

    from kairos.agents.roles.coder import Coder
    from kairos.core.message_bus import MessageBus

    bus = MessageBus()
    agent = Coder(
        agent_id="a1",
        llm_config=FakeLLM.config,
        message_bus=bus,
        tools=[],  # no tools
    )
    # Replace the provider with our fake.
    agent._llm = FakeLLM()

    task = AgentTask(id="t1", title="plan me", description="build something")
    result = await agent.run(task, plan_mode=True)

    # First LLM call MUST have had no tools.
    assert captured["tools"] is None
    assert "PLAN" in result