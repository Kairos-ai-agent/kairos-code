"""Round 8 — streaming verification.

Validates that the agent's streaming path actually publishes each
LLM token to the MessageBus as a ``stream.chunk`` event with the
right metadata, that tool_call sentinels are parsed, and that
fallback to ``complete()`` happens if the provider's stream raises.
"""
from __future__ import annotations

import asyncio
import json
from typing import List

import pytest

from kairos.agents.base import AgentTask, KairosAgent
from kairos.core.message_bus import Message, MessageBus
from kairos.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from kairos.llm.provider_registry import ProviderRegistry


class _FakeLLM(BaseLLMProvider):
    """Mock LLM provider with controllable streaming behaviour."""

    def __init__(self, config: LLMConfig, chunks: List[str] | None = None,
                 fail_stream: bool = False):
        super().__init__(config)
        self._chunks = chunks or []
        self._fail_stream = fail_stream
        self.stream_called = 0
        self.complete_called = 0

    async def stream(self, messages, tools=None, temperature=None, max_tokens=None):
        self.stream_called += 1
        if self._fail_stream:
            raise RuntimeError("simulated stream failure")
        for c in self._chunks:
            yield c

    async def complete(self, messages, tools=None, temperature=None, max_tokens=None):
        self.complete_called += 1
        return LLMResponse(
            content="fallback text", model="fake", usage={}, finish_reason="stop",
        )

    async def close(self):
        pass


# Register the fake provider once. The test reads the registered class
# via the registry; ``_make_agent`` swaps the instance after construction
# so the registry lookup at __init__ time is only used to validate the
# name exists.
ProviderRegistry.register("streaming_fake", _FakeLLM)


def _make_agent(bus: MessageBus, llm: BaseLLMProvider, agent_id: str = "a1") -> KairosAgent:
    agent = KairosAgent(
        agent_id=agent_id, name="coder", role="coder",
        system_prompt="sys",
        llm_config=LLMConfig(provider="streaming_fake", model="m"),
        message_bus=bus,
    )
    agent._llm = llm
    return agent


def _attach_listener(bus: MessageBus, topic: str) -> tuple:
    """Attach a listener that captures messages matching *topic*."""
    captured: List[Message] = []

    async def listener(msg):
        if msg.topic == topic:
            captured.append(msg)

    token = bus.add_listener(listener)
    return token, captured


# ---------------------------------------------------------------------------
# Basic streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_publishes_each_chunk_to_message_bus():
    """Each token yielded by the LLM appears as a stream.chunk message,
    and the final LLMResponse concatenates them in order."""
    bus = MessageBus()
    token, chunks = _attach_listener(bus, "stream.chunk")
    try:
        llm = _FakeLLM(LLMConfig(provider="streaming_fake", model="m"),
                       chunks=["Hello", ", ", "world", "!"])
        agent = _make_agent(bus, llm)
        task = AgentTask(id="t1", title="t", description="say hi")
        result = await agent._stream_complete(
            messages=[LLMMessage(role="user", content="hi")],
            tools=None, task=task, turn_no=1,
        )
        await asyncio.sleep(0.05)
    finally:
        bus.remove_listener(token)

    assert llm.stream_called == 1
    assert llm.complete_called == 0
    assert result.content == "Hello, world!"
    assert [m.content for m in chunks] == ["Hello", ", ", "world", "!"]


@pytest.mark.asyncio
async def test_stream_chunk_metadata_increments_seq():
    """stream.chunk messages carry task_id / turn / seq / accumulated_len."""
    bus = MessageBus()
    token, captured = _attach_listener(bus, "stream.chunk")
    try:
        llm = _FakeLLM(LLMConfig(provider="streaming_fake", model="m"),
                       chunks=["a", "b", "c"])
        agent = _make_agent(bus, llm, agent_id="a2")
        task = AgentTask(id="tt", title="t", description="d")
        await agent._stream_complete(
            messages=[LLMMessage(role="user", content="x")],
            tools=None, task=task, turn_no=7,
        )
        await asyncio.sleep(0.05)
    finally:
        bus.remove_listener(token)

    assert len(captured) == 3
    expected = ["a", "b", "c"]
    running = 0
    for i, m in enumerate(captured, start=1):
        assert m.metadata["task_id"] == "tt"
        assert m.metadata["turn"] == 7
        assert m.metadata["seq"] == i
        running += len(expected[i - 1])
        assert m.metadata["accumulated_len"] == running
    assert "".join(m.content for m in captured) == "abc"


# ---------------------------------------------------------------------------
# tool_calls sentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_parses_tool_calls_sentinel():
    """A final JSON ``{"type":"tool_calls", "tool_calls": [...]}`` is
    parsed into LLMResponse.tool_calls and not leaked into content."""
    sentinel = json.dumps({
        "type": "tool_calls",
        "tool_calls": [
            {"id": "c1", "name": "search", "arguments": {"q": "rust"}},
        ],
    })
    bus = MessageBus()
    llm = _FakeLLM(LLMConfig(provider="streaming_fake", model="m"),
                   chunks=["Some prose. ", sentinel])
    agent = _make_agent(bus, llm, agent_id="a3")
    task = AgentTask(id="t3", title="t", description="d")
    resp = await agent._stream_complete(
        messages=[LLMMessage(role="user", content="x")],
        tools=None, task=task, turn_no=1,
    )
    assert resp.content == "Some prose. "
    assert "tool_calls" not in resp.content
    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "search"
    assert resp.tool_calls[0].arguments == {"q": "rust"}


# ---------------------------------------------------------------------------
# Fallback when stream raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_raises_falls_back_to_complete():
    """If the LLM's stream() raises, _stream_complete falls back to
    complete() so the user still gets an answer."""
    bus = MessageBus()
    llm = _FakeLLM(LLMConfig(provider="streaming_fake", model="m"),
                   fail_stream=True)
    agent = _make_agent(bus, llm, agent_id="a4")
    task = AgentTask(id="t4", title="t", description="d")
    resp = await agent._stream_complete(
        messages=[LLMMessage(role="user", content="x")],
        tools=None, task=task, turn_no=1,
    )
    assert llm.complete_called == 1
    assert resp.content == "fallback text"


# ---------------------------------------------------------------------------
# WS envelope shape
# ---------------------------------------------------------------------------


def test_message_to_dict_preserves_stream_chunk_metadata():
    """The MessageBus→WebSocket envelope preserves the metadata fields
    the frontend needs (task_id, turn, seq, accumulated_len) so it can
    dedupe chunks and animate the typewriter effect."""
    m = Message(
        sender="coder", topic="stream.chunk", content="x",
        msg_type="text",
        metadata={"task_id": "abc", "turn": 3, "seq": 4, "accumulated_len": 12},
    )
    d = m.to_dict()
    assert d["topic"] == "stream.chunk"
    assert d["content"] == "x"
    assert d["metadata"]["task_id"] == "abc"
    assert d["metadata"]["turn"] == 3
    assert d["metadata"]["seq"] == 4
    assert d["metadata"]["accumulated_len"] == 12
