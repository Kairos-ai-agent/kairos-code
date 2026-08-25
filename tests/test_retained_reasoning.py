"""Tests for retained reasoning (Codex-Harness-style memory summarization)."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.agents.base import AgentTask, KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.provider_registry import ProviderRegistry


class StubProvider:
    """LLM provider that just echoes a canned summary and counts calls."""

    def __init__(self, summary: str = "STUB-SUMMARY"):
        self.config = LLMConfig(provider="stub", model="stub", api_key="sk")
        self.summary_text = summary
        self.call_count = 0

    async def complete(self, messages, tools=None, **kw):
        self.call_count += 1
        return LLMResponse(
            content=self.summary_text, model="stub", usage={},
            finish_reason="stop",
        )

    async def stream(self, messages, **kw):
        yield "stub"

    async def close(self):
        pass


# Register stub once at module import so individual tests don't have to.
ProviderRegistry.register("stub", StubProvider)


def _make_agent(provider, role="test", keep_recent=4, summarize_every=2):
    bus = MessageBus()
    agent = KairosAgent(
        agent_id=f"test.{role}",
        name=role.title(),
        role=role,
        system_prompt="You are a test agent.",
        llm_config=provider.config,
        message_bus=bus,
        tools=[],
    )
    agent._llm = provider
    agent._keep_recent = keep_recent
    agent._summarize_every_n = summarize_every
    # Avoid loading AGENTS.md/Skills (no project_dir).
    agent._agents_md_loader = None
    agent._skills_loader = None
    return agent


def test_summarize_skipped_when_below_keep_recent():
    provider = StubProvider()
    agent = _make_agent(provider, keep_recent=4, summarize_every=1)
    # Stuff 3 messages (under keep_recent=4).
    for i in range(3):
        agent._memory.append(LLMMessage(role="user", content=f"m{i}"))
    asyncio.run(agent._maybe_summarize_memory(current_turn=1))
    assert provider.call_count == 0
    assert agent._memory_summary == ""


def test_summarize_runs_every_n_turns():
    provider = StubProvider("SUMMARY-A")
    agent = _make_agent(provider, keep_recent=2, summarize_every=2)
    for i in range(6):
        agent._memory.append(LLMMessage(role="user", content=f"m{i}"))

    asyncio.run(agent._maybe_summarize_memory(current_turn=1))   # too early
    assert provider.call_count == 0
    asyncio.run(agent._maybe_summarize_memory(current_turn=2))   # trigger
    assert provider.call_count == 1
    assert agent._memory_summary == "SUMMARY-A"
    asyncio.run(agent._maybe_summarize_memory(current_turn=3))   # too early
    assert provider.call_count == 1
    asyncio.run(agent._maybe_summarize_memory(current_turn=4))   # trigger again
    assert provider.call_count == 2


def test_summarize_runs_when_memory_overflows_budget():
    provider = StubProvider("EMERGENCY-SUMMARY")
    agent = _make_agent(provider, keep_recent=2, summarize_every=999)
    agent._max_tokens = 100  # tiny budget
    # Each message is ~30 tokens, so 5 messages >> 80% of 100.
    for i in range(6):
        agent._memory.append(LLMMessage(role="user", content="x" * 100))
    asyncio.run(agent._maybe_summarize_memory(current_turn=1))   # early by turn, but over budget
    assert provider.call_count == 1
    assert agent._memory_summary == "EMERGENCY-SUMMARY"


def test_summarize_does_not_crash_on_provider_error():
    class BadProvider(StubProvider):
        async def complete(self, messages, **kw):
            raise RuntimeError("LLM down")
    agent = _make_agent(BadProvider(), keep_recent=2, summarize_every=1)
    for i in range(4):
        agent._memory.append(LLMMessage(role="user", content="x"))
    # Should not raise.
    asyncio.run(agent._maybe_summarize_memory(current_turn=5))
    assert agent._memory_summary == ""  # unchanged


def test_build_messages_includes_summary_as_second_system_msg():
    provider = StubProvider()
    agent = _make_agent(provider, keep_recent=2)
    agent._memory_summary = "Earlier: did X then Y."
    agent._memory = [
        LLMMessage(role="user", content="turn 1"),
        LLMMessage(role="assistant", content="answer 1"),
    ]
    msgs = agent._build_messages()
    assert msgs[0].role == "system"
    assert msgs[0].content == "You are a test agent."  # base prompt
    assert msgs[1].role == "system"
    assert "Earlier: did X then Y." in msgs[1].content
    assert "compact summary" in msgs[1].content  # the header text
    assert msgs[2].role == "user"
    assert msgs[2].content == "turn 1"


def test_build_messages_omits_summary_when_empty():
    provider = StubProvider()
    agent = _make_agent(provider, keep_recent=2)
    agent._memory_summary = ""
    msgs = agent._build_messages()
    # Only the base system message should be present.
    assert len(msgs) == 1
    assert msgs[0].role == "system"
