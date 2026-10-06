"""``agent.thinking`` — the model's reasoning, published as a live tail.

Nothing published this topic before: the provider counted hidden reasoning
(``reasoning_chars``) and threw the text away, and two other modules merely
*referenced* the topic (``api/routes/websocket.py`` AGENT_STATE_TRIGGERS,
``Persistence.CHAT_TOPICS``). The UI's "thinking" line therefore had no real
source. These tests pin the new contract:

  1. a streaming turn publishes ``agent.thinking`` whose content is the TAIL of
     the reasoning, not the whole thing and not a per-token frame;
  2. it is throttled — dense deltas do NOT become one message each;
  3. the reasoning text is NEVER part of the reply (``LLMResponse.content``) —
     the invariant the provider comments have guarded all along;
  4. the non-streaming ``complete()`` path (what ``chat()`` uses) publishes one
     hint too, because there the whole reasoning arrives in a single piece;
  5. mid-stream hints are marked ``transient`` and are not persisted.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import List

from kairos.agents.agent_parts.llm import AGENT_THINKING_TAIL_CHARS
from kairos.agents.base import KairosAgent
from kairos.core.message_bus import Message, MessageBus
from kairos.core.orchestrator_parts.context import OrchContextMixin
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse
from kairos.llm.providers.openai_provider import OpenAIProvider


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _agent(llm) -> tuple:
    """An agent with a fake provider and a capturing bus."""
    bus = MessageBus()
    cfg = LLMConfig(provider="openai", model="stub-model", api_key="sk-test")
    agent = KairosAgent(
        agent_id="p1.coder", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg, message_bus=bus, tools=[],
    )
    agent._llm = llm
    return agent, bus


def _capture(bus: MessageBus, topic: str) -> List[Message]:
    captured: List[Message] = []

    async def listener(msg):
        if msg.topic == topic:
            captured.append(msg)

    bus.add_listener(listener)
    return captured


def _reasoning_envelope(text: str) -> str:
    return json.dumps({"type": "reasoning", "text": text})


class _StubStreamLLM:
    """A provider whose ``stream()`` yields exactly the given chunks."""

    def __init__(self, chunks: List[str], complete_response=None):
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
    """A provider with no streaming (``chat()`` only needs ``complete()``)."""

    def __init__(self, response: LLMResponse):
        self._response = response

    async def complete(self, messages, tools=None, **kw):
        return self._response

    async def close(self):
        pass


class _Task:
    """Minimal stand-in: ``_stream_complete`` only reads ``.id``."""

    id = "t1"


# ---------------------------------------------------------------------------
# 1 + 3 — streaming publishes the tail, and it never reaches the reply
# ---------------------------------------------------------------------------


def test_streaming_publishes_reasoning_tail_and_keeps_it_out_of_the_reply():
    reasoning = "甲" * 1000 + "最后的结论是：应该修 openai_provider"
    agent, bus = _agent(_StubStreamLLM([
        _reasoning_envelope(reasoning),
        "这是正文回答。",
        json.dumps({"type": "stream_meta", "finish_reason": "stop",
                    "reasoning_chars": len(reasoning), "usage": {}}),
    ]))
    captured = _capture(bus, "agent.thinking")

    resp = asyncio.run(agent._stream_complete([], None, _Task(), 3))

    # The reply is the content deltas only — the reasoning is not in it, and
    # neither is the envelope JSON.
    assert resp.content == "这是正文回答。"
    assert "最后的结论" not in resp.content
    assert '{"type"' not in resp.content

    # We did publish at least one hint, and its content is the tail.
    assert captured, "agent.thinking was never published"
    live = [m for m in captured if m.metadata.get("transient")]
    closing = [m for m in captured if m.metadata.get("thinking_done")]
    assert live, "no mid-stream agent.thinking hint was published"
    assert closing, "no closing agent.thinking hint was published"
    assert live[0].content == reasoning[-AGENT_THINKING_TAIL_CHARS:]
    assert len(live[0].content) <= AGENT_THINKING_TAIL_CHARS
    assert "最后的结论" in live[0].content
    # Metadata the frontend/ledger expect.
    assert live[0].metadata["task_id"] == "t1"
    assert live[0].metadata["turn"] == 3
    assert closing[0].metadata["reasoning_chars"] == len(reasoning)


def test_reasoning_never_appears_in_stream_chunk_events():
    """The content channel carries the answer only."""
    agent, bus = _agent(_StubStreamLLM([
        _reasoning_envelope("SECRET-REASONING"),
        "公开的正文",
    ]))
    chunks = _capture(bus, "stream.chunk")

    asyncio.run(agent._stream_complete([], None, _Task(), 1))

    joined = "".join(str(m.content) for m in chunks)
    assert "公开的正文" in joined
    assert "SECRET-REASONING" not in joined


# ---------------------------------------------------------------------------
# 2 — throttling
# ---------------------------------------------------------------------------


def test_dense_reasoning_deltas_are_throttled():
    """50 fast deltas must not become 50 WebSocket frames."""
    deltas = [f"思考第{i}步。" for i in range(50)]
    agent, bus = _agent(_StubStreamLLM(
        [_reasoning_envelope(d) for d in deltas] + ["answer"]))
    captured = _capture(bus, "agent.thinking")

    asyncio.run(agent._stream_complete([], None, _Task(), 1))

    transient = [m for m in captured if m.metadata.get("transient")]
    assert transient, "throttling dropped every hint"
    assert len(transient) < 10, (
        f"throttling ineffective: {len(transient)} hints for {len(deltas)} deltas"
    )
    # The last hint always carries the latest tail.
    assert len(captured[-1].content) <= AGENT_THINKING_TAIL_CHARS


# ---------------------------------------------------------------------------
# 4 — the non-streaming complete() path
# ---------------------------------------------------------------------------


def test_complete_path_publishes_one_hint_with_total_chars():
    reasoning = "先读文件，再定位，最后给出结论。" * 40
    resp = LLMResponse(
        content="做完了。", model="deepseek", finish_reason="stop",
        usage={}, reasoning_chars=len(reasoning),
        reasoning_tail=reasoning[-400:],
    )
    agent, bus = _agent(_StubCompleteLLM(resp))
    captured = _capture(bus, "agent.thinking")

    reply = asyncio.run(agent.chat("帮我做这件事"))

    assert reply == "做完了。"
    assert len(captured) == 1, "the complete path must publish exactly one hint"
    assert captured[0].content == reasoning[-400:]
    assert captured[0].metadata["reasoning_chars"] == len(reasoning)
    # Not a stream-closing hint: there is no earlier tail to clear, so the UI
    # shows this text on the rolling line.
    assert captured[0].metadata.get("thinking_done") is not True
    assert captured[0].metadata.get("transient") is not True


def test_complete_path_without_reasoning_publishes_nothing():
    resp = LLMResponse(content="普通回答", model="deepseek", usage={})
    agent, bus = _agent(_StubCompleteLLM(resp))
    captured = _capture(bus, "agent.thinking")

    asyncio.run(agent.chat("hi"))

    assert captured == [], "a non-thinking model must not emit agent.thinking"


# ---------------------------------------------------------------------------
# 5 — transient hints are not persisted
# ---------------------------------------------------------------------------


def test_transient_thinking_is_not_persisted(tmp_path):
    db = Persistence(tmp_path / "t.db")

    class _Host:
        _db = db

    host = _Host()
    OrchContextMixin._persist_message(host, Message(
        sender="p1.coder", topic="agent.thinking", content="mid-stream tail",
        metadata={"project_id": "p1", "transient": True}))
    OrchContextMixin._persist_message(host, Message(
        sender="p1.coder", topic="agent.thinking", content="closing tail",
        metadata={"project_id": "p1", "thinking_done": True}))

    rows = db.load_messages(project_id="p1", chat_only=True)
    contents = [r["content"] for r in rows]
    assert "closing tail" in contents
    assert "mid-stream tail" not in contents, (
        "transient hints would flood the DB and the reloaded thread"
    )


# ---------------------------------------------------------------------------
# provider level: the envelope exists, and complete() carries the tail
# ---------------------------------------------------------------------------


def _fake_chunk(content=None, reasoning=None, finish=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning,
                            tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None,
    )


class _FakeAsyncStream:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeCompletions:
    def __init__(self, stream_items=None, message=None):
        self._stream_items = stream_items or []
        self._message = message

    async def create(self, **kwargs):
        if kwargs.get("stream"):
            return _FakeAsyncStream(self._stream_items)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self._message, finish_reason="stop")],
            model="stub", usage=None,
        )


class _FakeClient:
    def __init__(self, stream_items=None, message=None):
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(stream_items, message))


def _provider():
    return OpenAIProvider(LLMConfig(provider="openai", model="m",
                                    api_key="sk-test"))


def test_provider_stream_emits_reasoning_envelope_not_content():
    provider = _provider()
    provider._client = _FakeClient(stream_items=[
        _fake_chunk(reasoning="想"),
        _fake_chunk(reasoning="一想"),
        _fake_chunk(content="答案"),
        _fake_chunk(finish="stop"),
    ])

    async def collect():
        return [c async for c in provider.stream(
            [LLMMessage(role="user", content="hi")])]

    raw = asyncio.run(collect())
    envelopes = []
    for c in raw:
        if not (isinstance(c, str) and c.startswith("{")):
            continue
        try:
            parsed = json.loads(c)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "reasoning":
            envelopes.append(parsed)
    assert [e["text"] for e in envelopes] == ["想", "一想"]
    # The envelope is NOT rendered as reply text: the only plain-text delta the
    # provider emits is the answer.
    text_deltas = [c for c in raw
                   if not (isinstance(c, str) and c.startswith("{"))]
    assert text_deltas == ["答案"]


def test_provider_complete_sets_reasoning_tail_and_keeps_content_clean():
    provider = _provider()
    reasoning = "R" * 1000
    provider._client = _FakeClient(message=SimpleNamespace(
        content="最终答案", tool_calls=None, reasoning_content=reasoning))

    resp = asyncio.run(provider.complete([LLMMessage(role="user", content="hi")]))

    assert resp.content == "最终答案"
    assert reasoning not in resp.content
    assert resp.reasoning_chars == 1000
    assert resp.reasoning_tail == "R" * AGENT_THINKING_TAIL_CHARS
    assert len(resp.reasoning_tail) == AGENT_THINKING_TAIL_CHARS


def test_publish_thinking_helper_is_tail_only():
    """A direct call clamps to the tail, whatever the caller hands it."""
    agent, bus = _agent(_StubCompleteLLM(LLMResponse(content="", model="x")))
    captured = _capture(bus, "agent.thinking")

    asyncio.run(agent._publish_thinking(
        "X" * 5000, task_id="t9", turn=2, reasoning_chars=5000))

    assert len(captured) == 1
    assert len(captured[0].content) == AGENT_THINKING_TAIL_CHARS
    assert captured[0].content.endswith("X")


def test_resilient_stream_forwards_the_reasoning_tail():
    """The default provider is wrapped in ResilientProvider, whose ``stream()``
    delegates to ``complete()``. Without forwarding the tail across that hop the
    agent loop would never see the reasoning and the UI line would stay empty."""
    from kairos.llm.resilient import ResilientProvider

    class _Primary:
        def __init__(self, resp):
            self._resp = resp
            self.config = LLMConfig(provider="openai", model="m", api_key="sk")

        async def complete(self, messages, tools=None, **kw):
            return self._resp

        async def stream(self, messages, tools=None, **kw):
            yield "x"

        async def close(self):
            pass

    resp = LLMResponse(content="答案", model="m", usage={},
                       reasoning_chars=1234, reasoning_tail="TAIL-OF-REASONING")
    rp = ResilientProvider(_Primary(resp))

    async def collect():
        return [c async for c in rp.stream([])]

    raw = asyncio.run(collect())
    envelopes = []
    for c in raw:
        if isinstance(c, str) and c.startswith("{"):
            envelopes.append(json.loads(c))
    assert any(e.get("type") == "reasoning"
               and e.get("text") == "TAIL-OF-REASONING" for e in envelopes)
    text = "".join(c for c in raw if not (isinstance(c, str) and c.startswith("{")))
    assert "答案" in text
    assert "TAIL-OF-REASONING" not in text, "reasoning leaked into the reply"
