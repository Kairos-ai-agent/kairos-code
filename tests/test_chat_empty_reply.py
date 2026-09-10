"""Regression: a chat turn with no content must never be silent.

A thinking / reasoning model can spend the whole completion budget on
hidden reasoning and return ``content=""`` with ``finish_reason="length"``
(every completion token counted as ``reasoning_tokens``). Before this fix
``/api/projects/{id}/chat`` answered HTTP 200 with ``reply=""``, so the UI
rendered NOTHING at all — the user saw a chat that never replies
("普通chat没有回复任何消息") with no error to explain why.

``KairosAgent.chat()`` must surface a readable notice (and publish it on
the bus, which the chat thread renders) instead of returning "".
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMResponse


class _StubLLM:
    def __init__(self, response: LLMResponse):
        self._response = response
        self.calls: List[Dict[str, Any]] = []

    async def complete(self, messages, tools=None, **kw):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self._response

    async def close(self):
        pass


def _make_agent(response: LLMResponse) -> tuple[KairosAgent, MessageBus]:
    bus = MessageBus()
    cfg = LLMConfig(provider="openai", model="deepseek-v4.1-flash-expires-on-0910",
                    api_key="sk-test", base_url="https://api.deepseek.com/v1")
    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg, message_bus=bus, tools=[],
    )
    agent._llm = _StubLLM(response)
    return agent, bus


def _chat_notices(bus: MessageBus) -> List[str]:
    msgs = asyncio.run(bus.recent(limit=50))
    return [m.content for m in msgs if getattr(m, "topic", "") == "agent.chat"]


def test_empty_thinking_only_reply_surfaces_notice():
    """reasoning-only reply (content empty, finish_reason=length) -> notice."""
    resp = LLMResponse(
        content="", model="deepseek-flash", finish_reason="length",
        usage={"completion_tokens": 8192,
               "completion_tokens_details": {"reasoning_tokens": 8192}},
    )
    agent, bus = _make_agent(resp)

    reply = asyncio.run(agent.chat("新建一个html，做一个2d 小橘猫骑自行车的动态svg"))

    assert reply.strip(), "an empty reply must never be returned to the UI"
    assert "deepseek-v4.1-flash-expires-on-0910" in reply
    assert "reasoning_tokens=8192" in reply
    # It must also be published on the bus — that is what the chat thread renders.
    assert reply in _chat_notices(bus)


def test_empty_reply_without_reasoning_still_surfaces_notice():
    """A blank reply for any other reason also gets a visible notice."""
    resp = LLMResponse(content="   ", model="m", finish_reason="stop", usage={})
    agent, bus = _make_agent(resp)

    reply = asyncio.run(agent.chat("hi"))

    assert reply.strip()
    assert reply in _chat_notices(bus)


def test_normal_reply_is_returned_unchanged():
    """The happy path is untouched (no notice wrapping)."""
    resp = LLMResponse(content="你好！有什么我可以帮你的吗？", model="m",
                       finish_reason="stop", usage={"completion_tokens": 14})
    agent, bus = _make_agent(resp)

    reply = asyncio.run(agent.chat("你好"))

    assert reply == "你好！有什么我可以帮你的吗？"
    assert "⚠️" not in reply
    assert reply in _chat_notices(bus)
