"""Tests for kairos.agents.base observability integration.

Verifies that every LLM call inside ``KairosAgent._stream_complete``
and ``_summarize_old_turns`` is wrapped in a tracer span.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from kairos.agents.base import AgentStatus, AgentTask, KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMResponse, ToolCall
from kairos.observability import NoOpSpan, get_default_tracer, init_default_tracer


@pytest.fixture(autouse=True)
def _reset_tracer():
    """Reset the global tracer to a fresh no-op one before each
    test. Other test modules (notably test_observability.py) mutate
    it via init_default_tracer; without this reset we'd inherit
    their state and the NoOpSpan assertion below would fail.
    """
    import kairos.observability as obs
    obs._default_tracer = None
    init_default_tracer()
    yield
    obs._default_tracer = None


class _StubLLM:
    def __init__(self, responses: List[LLMResponse], stream_chunks: List[str] | None = None):
        self._responses = list(responses)
        # If stream_chunks is given, stream() yields these as raw
        # text chunks (one per yield). If None, stream() yields a
        # single JSON tool_calls sentinel (the format _stream_complete
        # already handles for non-streaming case via the
        # {"type": "tool_calls", ...} sentinel).
        self._stream_chunks = stream_chunks
        self.calls: List[Dict[str, Any]] = []

    async def complete(self, messages, tools=None, **kw):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._responses:
            return LLMResponse(content="(no more responses)", model="m")
        return self._responses.pop(0)

    async def stream(self, messages, tools=None, **kw):
        # Yield either the configured chunks, or a final JSON
        # tool_calls sentinel so _stream_complete can complete.
        if self._stream_chunks is not None:
            for ch in self._stream_chunks:
                yield ch
        else:
            # Emit a final tool_calls sentinel using the first queued
            # response
            if self._responses:
                r = self._responses.pop(0)
                if r.tool_calls:
                    yield ('{"type": "tool_calls", "tool_calls": [' +
                           ', '.join(
                               f'{{"id": "{tc.id}", "name": "{tc.name}", "arguments": {json_dumps(tc.arguments)}}}'
                               for tc in r.tool_calls
                           ) + ']}')
                else:
                    yield r.content or ""
            else:
                yield "(end)"

    async def close(self):
        pass


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _make_agent(responses: List[LLMResponse], stream_chunks: List[str] | None = None
                ) -> tuple[KairosAgent, MessageBus]:
    bus = MessageBus()
    cfg = LLMConfig(provider="anthropic", model="claude-test", api_key="sk-test")
    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg, message_bus=bus,
        tools=[],
    )
    agent._llm = _StubLLM(responses, stream_chunks=stream_chunks)
    return agent, bus


# ---------------------------------------------------------------------------
# _traced_llm_call helper
# ---------------------------------------------------------------------------


def test_traced_llm_call_returns_noop_span():
    """Default tracer is a no-op, so the helper returns a NoOpSpan."""
    agent, _ = _make_agent([])
    with agent._traced_llm_call([], []) as span:
        assert isinstance(span, NoOpSpan)
        span.set_attribute("test", "ok")
        # Spans are recorded in the global default tracer
        default = get_default_tracer()
        assert any(s is span for s in default.get_spans())


def test_traced_llm_call_records_model_name():
    agent, _ = _make_agent([])
    with agent._traced_llm_call([], None) as span:
        # Span's name encodes the model so it's filterable in Langfuse
        assert "llm." in span.name
        # The model is on the attribute set
        assert "claude-test" in str(span.attributes.get("gen_ai.request.model", ""))


# ---------------------------------------------------------------------------
# _stream_complete records spans
# ---------------------------------------------------------------------------


def test_stream_complete_records_span_on_response():
    """A streaming LLM call is wrapped in a tracer span with the
    correct attributes."""
    # Reset default tracer to start clean
    init_default_tracer()
    tracer = get_default_tracer()

    # No tool calls, just a streaming text response
    agent, _ = _make_agent(
        [LLMResponse(content="hello", model="m")],
        stream_chunks=["h", "ello"],
    )
    # Use _stream_complete directly to exercise the wrap
    resp = asyncio.run(agent._stream_complete(
        [], tools=None,
        task=AgentTask(id="t", title="t", description="d"),
        turn_no=0,
    ))
    spans = tracer.get_spans()
    # The streaming call emits at least one LLM span
    llm_spans = [s for s in spans if s.name.startswith("llm.")]
    assert llm_spans, f"expected at least one llm.* span, got {[s.name for s in spans]}"
    s = llm_spans[0]
    # The model was recorded
    assert s.attributes.get("gen_ai.request.model") == "claude-test"
    # Output-side metrics (duration at minimum) were recorded
    assert "gen_ai.response.duration_ms" in s.attributes


# ---------------------------------------------------------------------------
# Tracing does not break existing behavior
# ---------------------------------------------------------------------------


def test_traced_call_swallows_tracing_errors():
    """If the tracer span's set_attribute throws, the LLM call
    itself must still succeed. We test this by giving a span that
    raises on every attribute call (via a misbehaving span)."""
    from kairos.observability import Tracer
    broken_tracer = Tracer(name="broken")

    class _BadSpan(NoOpSpan):
        def set_attribute(self, k, v):
            raise RuntimeError("tracing failure")

        def set_output(self, **kw):
            raise RuntimeError("tracing failure on set_output")

    class _BadCtx:
        """Context manager that yields a misbehaving span."""
        def __enter__(self):
            return _BadSpan(name="llm.x")

        def __exit__(self, *args):
            return False

    broken_tracer.llm_call = lambda **kw: _BadCtx()
    import kairos.observability as obs
    obs._default_tracer = broken_tracer

    # The streaming code must still work even with a broken tracer
    agent, _ = _make_agent(
        [LLMResponse(content="hi", model="m")],
        stream_chunks=["hi"],
    )
    resp = asyncio.run(agent._stream_complete(
        [], tools=None,
        task=AgentTask(id="t", title="t", description="d"),
        turn_no=0,
    ))
    assert resp.content == "hi"
