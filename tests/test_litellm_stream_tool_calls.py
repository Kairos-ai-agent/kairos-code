"""Streaming tool calls through the LiteLLM provider.

``LiteLLMProvider.stream()`` used to be broken for the *only* shape real
providers emit: a tool call spread across several chunks (id/name once, the
JSON ``arguments`` as string fragments) with a terminal chunk whose
``finish_reason == "tool_calls"`` but ``delta.tool_calls is None``. The old code
read the tool_calls off that terminal chunk itself — where they are absent — so
the sentinel was never emitted and the streamed tool call was silently dropped.

These tests pin the fix: the incremental deltas are folded by index into an
accumulator and emitted as the sentinel ``_stream_complete`` understands, in the
exact shape it expects:

    {"type": "tool_calls",
     "tool_calls": [{"id": <str>, "name": <str>, "arguments": <obj | str>}]}

No network: ``litellm`` is replaced with a fake whose ``acompletion`` returns an
async iterator of chunk objects (or dicts).
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse
from kairos.llm.providers import litellm_provider as lp

LOG = "kairos.llm.providers.litellm_provider"


# ---------------------------------------------------------------------------
# Fake litellm transport
# ---------------------------------------------------------------------------


class _FakeAsyncStream:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeLitellm:
    """Stand-in for the ``litellm`` module; ``acompletion`` yields our chunks."""

    def __init__(self, items):
        self._items = list(items)

    async def acompletion(self, **kw):
        return _FakeAsyncStream(self._items)


# -- object-shaped chunks (what litellm actually returns) -------------------


class _Func:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, index, id=None, function=None):
        self.index = index
        self.id = id
        self.function = function


def _obj_chunk(content=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None,
    )


# -- dict-shaped chunks (proxy transports / hand-built doubles) -------------


def _dict_chunk(content=None, tool_calls=None, finish=None):
    delta = {"content": content, "tool_calls": tool_calls}
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def _provider(monkeypatch, items, model="gpt-4o"):
    monkeypatch.setattr(lp, "_ensure_litellm", lambda: None)
    monkeypatch.setattr(lp, "litellm", _FakeLitellm(items))
    return lp.LiteLLMProvider(
        LLMConfig(provider="litellm", model=model, api_key="sk-test"))


def _collect(provider):
    async def run():
        return [c async for c in provider.stream(
            [LLMMessage(role="user", content="do it")],
            tools=[{"type": "function", "function": {"name": "search"}}],
        )]
    return asyncio.run(run())


def _sentinels(raw):
    out = []
    for c in raw:
        if isinstance(c, str) and c.startswith("{"):
            try:
                parsed = json.loads(c)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "tool_calls":
                out.append(parsed)
    return out


def _text(raw):
    return [c for c in raw if not (isinstance(c, str) and c.startswith("{"))]


# ---------------------------------------------------------------------------
# The core regression: incremental fragments + terminal chunk without tool_calls
# ---------------------------------------------------------------------------


def _incremental_tool_chunks():
    """Three delta chunks then a terminal chunk with tool_calls=None."""
    return [
        _obj_chunk(tool_calls=[_TC(
            0, id="call_1", function=_Func(name="search", arguments=""))]),
        _obj_chunk(tool_calls=[_TC(
            0, function=_Func(arguments='{"q": '))]),
        _obj_chunk(tool_calls=[_TC(
            0, function=_Func(arguments='"rust"}'))]),
        # Terminal chunk: finish_reason set, no tool_calls payload at all.
        _obj_chunk(content=None, tool_calls=None, finish="tool_calls"),
    ]


def test_incremental_tool_call_fragments_are_collapsed_into_a_sentinel(monkeypatch):
    chunks = _incremental_tool_chunks()
    # Document the shape that used to defeat the code: the terminal chunk,
    # the only one with finish_reason="tool_calls", carries NO tool_calls.
    assert chunks[-1].choices[0].delta.tool_calls is None

    raw = _collect(_provider(monkeypatch, chunks))

    sents = _sentinels(raw)
    assert len(sents) == 1, f"expected exactly one tool_calls sentinel, got {raw!r}"
    tcs = sents[0]["tool_calls"]
    assert len(tcs) == 1
    assert tcs[0]["id"] == "call_1"
    assert tcs[0]["name"] == "search"
    # The arguments are the *concatenation* of the three fragments, parsed.
    assert tcs[0]["arguments"] == {"q": "rust"}
    # No visible text was leaked (provided no content deltas).
    assert _text(raw) == []


def test_dict_shaped_delta_accumulates_the_same_way(monkeypatch):
    chunks = [
        _dict_chunk(tool_calls=[{"index": 0, "id": "call_9",
                                 "function": {"name": "lookup", "arguments": '{"a":'}}]),
        _dict_chunk(tool_calls=[{"index": 0, "function": {"arguments": " 1}"}}]),
        _dict_chunk(tool_calls=None, finish="tool_calls"),
    ]
    raw = _collect(_provider(monkeypatch, chunks))

    sents = _sentinels(raw)
    assert len(sents) == 1, raw
    tc = sents[0]["tool_calls"][0]
    assert (tc["id"], tc["name"]) == ("call_9", "lookup")
    assert tc["arguments"] == {"a": 1}


def test_multiple_tool_calls_are_kept_separate_by_index(monkeypatch):
    chunks = [
        _obj_chunk(tool_calls=[
            _TC(0, id="a", function=_Func(name="one", arguments='{"x":1}')),
            _TC(1, id="b", function=_Func(name="two", arguments='{"y":')),
        ]),
        _obj_chunk(tool_calls=[_TC(1, function=_Func(arguments='2}'))]),
        _obj_chunk(finish="tool_calls"),
    ]
    raw = _collect(_provider(monkeypatch, chunks))
    tcs = _sentinels(raw)[0]["tool_calls"]
    assert [t["name"] for t in tcs] == ["one", "two"]
    assert tcs[0]["arguments"] == {"x": 1}
    assert tcs[1]["arguments"] == {"y": 2}


def test_id_and_name_take_their_first_value(monkeypatch):
    chunks = [
        _obj_chunk(tool_calls=[_TC(0, id="first", function=_Func(name="keep", arguments="") )]),
        _obj_chunk(tool_calls=[_TC(0, id="second", function=_Func(name="ignore", arguments='{}'))]),
        _obj_chunk(finish="tool_calls"),
    ]
    raw = _collect(_provider(monkeypatch, chunks))
    tc = _sentinels(raw)[0]["tool_calls"][0]
    assert tc["id"] == "first"
    assert tc["name"] == "keep"


# ---------------------------------------------------------------------------
# No tool calls -> no sentinel
# ---------------------------------------------------------------------------


def test_no_tool_calls_emits_no_sentinel(monkeypatch):
    chunks = [
        _obj_chunk(content="Hello"),
        _obj_chunk(content=" world"),
        _obj_chunk(finish="stop"),
    ]
    raw = _collect(_provider(monkeypatch, chunks))
    assert _sentinels(raw) == []
    assert _text(raw) == ["Hello", " world"]


# ---------------------------------------------------------------------------
# Malformed input: no crash, and a log trace (never a silent swallow)
# ---------------------------------------------------------------------------


def test_delta_none_is_skipped_with_a_log(monkeypatch, caplog):
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=None, finish_reason=None)]),
        _obj_chunk(content="ok"),
    ]
    with caplog.at_level(logging.DEBUG, logger=LOG):
        raw = _collect(_provider(monkeypatch, chunks))
    assert _text(raw) == ["ok"]
    assert any("had no delta" in r.message for r in caplog.records), caplog.text


def test_empty_choices_is_skipped_with_a_log(monkeypatch, caplog):
    chunks = [
        SimpleNamespace(choices=[], usage=None),
        _obj_chunk(content="ok"),
    ]
    with caplog.at_level(logging.DEBUG, logger=LOG):
        raw = _collect(_provider(monkeypatch, chunks))
    assert _text(raw) == ["ok"]
    assert any("no choices" in r.message for r in caplog.records), caplog.text


def test_broken_json_arguments_are_forwarded_with_a_log(monkeypatch, caplog):
    chunks = [
        _obj_chunk(tool_calls=[_TC(
            0, id="c", function=_Func(name="bad", arguments="{not json"))]),
        _obj_chunk(finish="tool_calls"),
    ]
    with caplog.at_level(logging.DEBUG, logger=LOG):
        raw = _collect(_provider(monkeypatch, chunks))
    tcs = _sentinels(raw)[0]["tool_calls"]
    # Forwarded verbatim rather than dropped.
    assert tcs[0]["arguments"] == "{not json"
    assert any("not valid JSON" in r.message for r in caplog.records), caplog.text


def test_tool_call_delta_missing_function_does_not_crash(monkeypatch):
    chunks = [
        _obj_chunk(tool_calls=[SimpleNamespace(index=0, id="z", function=None)]),
        _obj_chunk(finish="tool_calls"),
    ]
    raw = _collect(_provider(monkeypatch, chunks))
    tcs = _sentinels(raw)[0]["tool_calls"]
    assert tcs[0] == {"id": "z", "name": "", "arguments": {}}


def test_unknown_chunk_shape_does_not_crash(monkeypatch, caplog):
    chunks = [
        SimpleNamespace(),                 # no .choices at all
        "weird-string",                    # not a chunk object
        None,                              # nothing
        _obj_chunk(content="done"),
    ]
    with caplog.at_level(logging.DEBUG, logger=LOG):
        raw = _collect(_provider(monkeypatch, chunks))
    assert _text(raw) == ["done"]
    # At least one trace for the malformed chunks.
    assert caplog.records, "malformed chunks were swallowed silently"


# ---------------------------------------------------------------------------
# Non-tool stream is unchanged: text still streams through, delta by delta
# ---------------------------------------------------------------------------


def test_plain_text_stream_is_unchanged(monkeypatch):
    chunks = [
        _obj_chunk(content="Hello"),
        _obj_chunk(content=", "),
        _obj_chunk(content="world"),
        _obj_chunk(finish="stop"),
    ]
    raw = _collect(_provider(monkeypatch, chunks))
    assert raw == ["Hello", ", ", "world"]
    assert _sentinels(raw) == []


# ---------------------------------------------------------------------------
# End-to-end: the sentinel the provider emits is parsed by _stream_complete
# ---------------------------------------------------------------------------


class _ProviderBackedLLM:
    """An LLM whose ``stream()`` is the real provider's ``stream()``."""

    def __init__(self, provider):
        self._provider = provider

    async def stream(self, messages, tools=None, **kw):
        async for c in self._provider.stream(messages, tools=tools):
            yield c

    async def complete(self, messages, tools=None, **kw):
        return LLMResponse(content="", model="litellm")

    async def close(self):
        pass


class _Task:
    id = "t1"


def test_stream_complete_parses_the_provider_sentinel_end_to_end(monkeypatch):
    provider = _provider(monkeypatch, _incremental_tool_chunks())
    bus = MessageBus()
    agent = KairosAgent(
        agent_id="p1.coder", name="coder", role="coder",
        system_prompt="sys",
        llm_config=LLMConfig(provider="openai", model="stub", api_key="sk-test"),
        message_bus=bus, tools=[],
    )
    agent._llm = _ProviderBackedLLM(provider)

    resp = asyncio.run(agent._stream_complete([], None, _Task(), 1))

    assert resp.tool_calls is not None, "the streamed tool call was dropped"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "search"
    assert resp.tool_calls[0].id == "call_1"
    assert resp.tool_calls[0].arguments == {"q": "rust"}
    # The sentinel JSON never leaks into the visible reply.
    assert "tool_calls" not in (resp.content or "")
