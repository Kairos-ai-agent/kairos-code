"""The streaming path must report why it stopped and what it cost.

``_stream_complete`` is what the Coder/Reviewer loop uses; ``chat()`` uses the
non-streaming ``complete()`` instead. The stream used to yield nothing but text
deltas, so ``usage`` stayed ``{}`` and ``finish_reason`` stayed ``""`` on every
loop call: the cost ledger — the project's headline feature, "every token and
every dollar is written to a ledger you can audit" — recorded **0 tokens** for
every streamed round, and an empty answer could not say whether the model was
cut off or simply had nothing to say.

The stream contract is "plain text deltas plus typed JSON envelopes". These
tests pin both halves: a known envelope is consumed and never rendered, while
unknown JSON is still rendered as text (that used to be the only behaviour, and
a reply is allowed to contain JSON).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMResponse


class _Task:
    """Minimal stand-in: ``_stream_complete`` only reads ``.id``."""

    id = "t1"


class _StubStreamLLM:
    """A provider whose ``stream()`` yields exactly the given chunks."""

    def __init__(self, chunks: List[str], complete_response: LLMResponse = None):
        self._chunks = list(chunks)
        self._complete_response = complete_response or LLMResponse(
            content="", model="stub")

    async def stream(self, messages, tools=None, **kw):
        for chunk in self._chunks:
            yield chunk

    async def complete(self, messages, tools=None, **kw):
        return self._complete_response

    async def close(self):
        pass


class _StubCompleteLLM:
    """A provider with no streaming at all (chat() only needs complete())."""

    def __init__(self, response: LLMResponse):
        self._response = response

    async def complete(self, messages, tools=None, **kw):
        return self._response

    async def close(self):
        pass


def _agent(llm) -> tuple[KairosAgent, MessageBus]:
    bus = MessageBus()
    cfg = LLMConfig(provider="openai", model="stub-model", api_key="sk-test")
    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg, message_bus=bus, tools=[],
    )
    agent._llm = llm
    return agent, bus


def _stream(agent) -> LLMResponse:
    return asyncio.run(agent._stream_complete([], None, _Task(), 1))


def _meta(**kw) -> str:
    payload = {"type": "stream_meta", "finish_reason": "",
               "reasoning_chars": 0, "usage": {}}
    payload.update(kw)
    return json.dumps(payload)


def test_meta_envelope_is_consumed_and_not_rendered():
    """The metadata is for the caller, never part of the reply."""
    agent, _ = _agent(_StubStreamLLM([
        "你好",
        _meta(finish_reason="stop",
              usage={"prompt_tokens": 120, "completion_tokens": 8}),
    ]))
    resp = _stream(agent)

    assert resp.content == "你好"
    assert '{"type"' not in resp.content, "the envelope leaked into the reply"
    assert resp.finish_reason == "stop", (
        "without finish_reason an empty reply cannot be explained"
    )
    assert resp.usage == {"prompt_tokens": 120, "completion_tokens": 8}, (
        "without usage the cost ledger records 0 tokens for every streamed call"
    )


def test_hidden_reasoning_is_counted_and_kept_out_of_the_reply():
    """A thinking model that answers nothing must still be diagnosable."""
    agent, _ = _agent(_StubStreamLLM([
        _meta(finish_reason="length", reasoning_chars=4321),
    ]))
    resp = _stream(agent)

    assert resp.content == ""
    assert resp.reasoning_chars == 4321
    assert resp.finish_reason == "length"


def test_tool_calls_envelope_still_parses():
    """The pre-existing envelope type must keep working."""
    agent, _ = _agent(_StubStreamLLM([
        "先看看",
        json.dumps({"type": "tool_calls", "tool_calls": [
            {"id": "c1", "name": "file_read", "arguments": '{"path": "a.py"}'},
        ]}),
        _meta(finish_reason="tool_calls", usage={"prompt_tokens": 10}),
    ]))
    resp = _stream(agent)

    assert resp.content == "先看看"
    assert resp.tool_calls and resp.tool_calls[0].name == "file_read"
    assert resp.tool_calls[0].arguments == {"path": "a.py"}
    assert resp.finish_reason == "tool_calls"
    assert resp.usage == {"prompt_tokens": 10}


def test_unknown_json_is_still_rendered_as_text():
    """A stricter envelope branch must not start eating legitimate JSON."""
    agent, _ = _agent(_StubStreamLLM(['{"answer": 42}', "，就这样"]))
    resp = _stream(agent)

    assert resp.content == '{"answer": 42}，就这样'


def test_broken_json_is_still_rendered_as_text():
    agent, _ = _agent(_StubStreamLLM(['{"answer": ', "oops"]))
    resp = _stream(agent)

    assert resp.content == '{"answer": oops'


def test_reasoning_chars_explains_an_empty_chat_reply():
    """Providers that report hidden reasoning but no ``reasoning_tokens``
    (any endpoint not returning completion token details) still get the
    accurate notice instead of the vague "returned nothing" one."""
    resp = LLMResponse(
        content="", model="deepseek-flash", finish_reason="length",
        usage={}, reasoning_chars=1234,
    )
    agent, _ = _agent(_StubCompleteLLM(resp))

    reply = asyncio.run(agent.chat("写一首诗"))

    assert reply.strip(), "an empty reply must never be returned to the UI"
    assert "1234" in reply, "the notice must name what the budget was spent on"
    assert "思考" in reply
    assert "finish_reason=length" not in reply, (
        "the vague notice was used even though the reasoning was measurable"
    )
