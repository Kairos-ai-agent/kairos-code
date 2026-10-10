"""Local small-model compatibility (LM Studio / Ollama class endpoints).

The failure this pins, from a real session against a local OpenAI-compatible
server behind an 8192-token window:

* ``error: request (8208 tokens) exceeds the available context size (8192
  tokens)`` — the assembled prompt (system + history + the full ~22-tool
  catalogue) crossed the window and the whole call was rejected;
* ``"content": "", "reasoning_content": "The user asked a direct question…(the
  whole answer)", "finish_reason": "stop"`` — the model wrote its answer into
  the hidden-reasoning channel, and Kairos read only ``content``, so the user saw
  "模型没有返回任何内容";
* ``Client disconnected. Stopping generation...`` — the 120s default timeout cut
  a slow local turn off mid-answer.

Four fixes, one file:

1. reasoning-channel handling — empty content + non-empty reasoning does NOT
   get silently promoted into the answer (that is how the model's private
   monologue reached the user); the reply stays empty and the event is flagged
   (``reply_from_reasoning``) so the caller can retry / explain it;
2. prompt-budget fitting — trim the request BEFORE sending;
3. light tool mode — advertise only the core tools to a small model;
4. per-provider timeout — configurable, with 0/negative meaning "no limit".

Plus a source-level guard (the convention of tests/test_r37_ui_source.py): the
provider must NEVER copy the reasoning channel into ``content`` — an empty
``content`` stays empty and is marked, so the answer channel is the answer and
only the answer.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from kairos.agents.base import KairosAgent
from kairos.context_governor import (
    CONTEXT_TRIM_NOTICE,
    BudgetReport,
    approx_prompt_tokens,
    fit_to_budget,
)
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse
from kairos.llm.errors import context_limit_of, is_context_length_error
from kairos.llm.providers.openai_provider import OpenAIProvider
from kairos.tools.light_mode import CORE_TOOL_NAMES, is_light_mode

REPO_ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# helpers
# ===========================================================================


class _FakeTool:
    """Just enough for ``_get_tool_schemas`` to build a real schema list."""

    def __init__(self, name: str):
        self.name = name

    def to_schema(self) -> Dict[str, Any]:
        return {"name": self.name, "description": f"{self.name} tool",
                "parameters": {"type": "object", "properties": {}}}


def _agent(cfg: LLMConfig, tools: List[Any] | None = None) -> KairosAgent:
    return KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="sys", llm_config=cfg,
        message_bus=MessageBus(), tools=tools or [],
    )


def _fake_chunk(content=None, reasoning=None, finish=None, tool_calls=None,
                reasoning_alias=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning,
                            reasoning=reasoning_alias, tool_calls=tool_calls)
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


def _provider() -> OpenAIProvider:
    return OpenAIProvider(LLMConfig(provider="openai", model="local-model",
                                    api_key="sk-test"))


def _collect_stream(provider, messages):
    async def collect():
        return [c async for c in provider.stream(messages)]
    return asyncio.run(collect())


def _text_deltas(raw):
    return [c for c in raw if not (isinstance(c, str) and c.startswith("{"))]


def _envelopes(raw):
    out = []
    for c in raw:
        if isinstance(c, str) and c.startswith("{"):
            try:
                out.append(json.loads(c))
            except ValueError:
                pass
    return out


# ===========================================================================
# ① reasoning-channel handling (empty content is NOT promoted to the answer)
# ===========================================================================


def test_empty_content_with_reasoning_is_not_passed_off_as_the_answer():
    """The LM Studio shape: content="", reasoning_content=<the answer>.

    The reasoning is NOT the reply: the provider leaves ``content`` empty and
    flags it, so a caller can retry / explain it instead of the user being
    handed the model's private monologue.
    """
    answer = "The user asked a direct question. The answer is 42."
    provider = _provider()
    provider._client = _FakeClient(message=SimpleNamespace(
        content="", tool_calls=None, reasoning_content=answer))

    resp = asyncio.run(provider.complete([LLMMessage(role="user", content="q")]))

    assert resp.content == "", "an empty content channel must stay empty"
    assert resp.reply_from_reasoning is True, "the event must be recorded for telemetry"
    # The count/tail are still populated as before (used by agent.thinking).
    assert resp.reasoning_chars == len(answer)
    assert resp.reasoning_tail  # non-empty


def test_whitespace_only_content_is_also_not_promoted():
    provider = _provider()
    provider._client = _FakeClient(message=SimpleNamespace(
        content="   \n ", tool_calls=None, reasoning_content="real answer"))

    resp = asyncio.run(provider.complete([LLMMessage(role="user", content="q")]))

    assert resp.content.strip() == ""
    assert resp.reply_from_reasoning is True


def test_reasoning_alias_field_is_honoured():
    """Some servers expose ``reasoning`` instead of ``reasoning_content``."""
    provider = _provider()
    provider._client = _FakeClient(message=SimpleNamespace(
        content="", tool_calls=None, reasoning="via the alias field"))

    resp = asyncio.run(provider.complete([LLMMessage(role="user", content="q")]))

    assert resp.content == ""
    assert resp.reply_from_reasoning is True


def test_nonempty_content_is_never_merged_with_reasoning():
    """Regression guard: content present => reasoning stays OUT of the reply."""
    provider = _provider()
    provider._client = _FakeClient(message=SimpleNamespace(
        content="最终答案", tool_calls=None, reasoning_content="R" * 500))

    resp = asyncio.run(provider.complete([LLMMessage(role="user", content="q")]))

    assert resp.content == "最终答案"
    assert "R" * 500 not in resp.content
    assert resp.reply_from_reasoning is False
    assert resp.reasoning_tail == "R" * 400  # tail preserved, still separate


def test_stream_with_only_reasoning_is_not_emitted_as_the_reply():
    """Streaming local model: no content delta at all, only reasoning."""
    provider = _provider()
    provider._client = _FakeClient(stream_items=[
        _fake_chunk(reasoning="思考"),
        _fake_chunk(reasoning="：答案是 42"),
        _fake_chunk(finish="stop"),
    ])

    raw = _collect_stream(provider, [LLMMessage(role="user", content="q")])

    text = "".join(_text_deltas(raw))
    assert text == "", "streamed reasoning must not surface as the reply"
    assert "答案是 42" not in text
    meta = [e for e in _envelopes(raw) if e.get("type") == "stream_meta"]
    assert meta and meta[-1]["reply_from_reasoning"] is True


def test_stream_with_content_does_not_fall_back():
    provider = _provider()
    provider._client = _FakeClient(stream_items=[
        _fake_chunk(reasoning="内部推理"),
        _fake_chunk(content="正文回答"),
        _fake_chunk(finish="stop"),
    ])

    raw = _collect_stream(provider, [LLMMessage(role="user", content="q")])

    assert _text_deltas(raw) == ["正文回答"]
    meta = [e for e in _envelopes(raw) if e.get("type") == "stream_meta"]
    assert meta[-1]["reply_from_reasoning"] is False


def test_stream_with_tool_call_is_not_treated_as_empty():
    """A tool call is a legitimate empty-content turn — do not inject reasoning."""
    provider = _provider()
    tc = SimpleNamespace(index=0, id="c1",
                         function=SimpleNamespace(name="file_read",
                                                  arguments='{"path": "a"}'))
    provider._client = _FakeClient(stream_items=[
        _fake_chunk(reasoning="I should read the file"),
        _fake_chunk(tool_calls=[tc], finish="tool_calls"),
    ])

    raw = _collect_stream(provider, [LLMMessage(role="user", content="q")])

    assert _text_deltas(raw) == [], "reasoning must not leak into a tool-call turn"
    meta = [e for e in _envelopes(raw) if e.get("type") == "stream_meta"]
    assert meta[-1]["reply_from_reasoning"] is False


# ===========================================================================
# ② prompt-budget fitting
# ===========================================================================


def _long_history(n: int = 40, size: int = 400) -> List[LLMMessage]:
    msgs = [LLMMessage(role="system", content="S" * 300)]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(LLMMessage(role=role, content=("x" * size) + f"#{i}"))
    msgs.append(LLMMessage(role="user", content="CURRENT-USER-MESSAGE"))
    return msgs


def test_no_budget_leaves_the_request_untouched():
    msgs = _long_history()
    out, tools, report = fit_to_budget(msgs, None, None)
    assert out == msgs
    assert report.trimmed is False
    out, tools, report = fit_to_budget(msgs, None, 0)
    assert out == msgs
    assert report.trimmed is False


def test_over_budget_request_is_trimmed_to_fit():
    msgs = _long_history()
    before = approx_prompt_tokens(msgs, None)
    budget = 1500
    assert before > budget, "the fixture must actually exceed the budget"

    out, tools, report = fit_to_budget(msgs, None, budget)

    assert report.trimmed is True
    assert report.dropped > 0
    # The whole point: the request now fits the window.
    assert report.approx_tokens_after <= budget
    assert approx_prompt_tokens(out, None) <= budget
    # The message being answered survives verbatim.
    contents = [m.content for m in out]
    assert "CURRENT-USER-MESSAGE" in contents
    # ...and the model is TOLD the transcript was cut (never a silent trim).
    notice = CONTEXT_TRIM_NOTICE.split("{", 1)[0].strip()
    assert any(notice[:24] in (m.content or "") for m in out), (
        "a trimmed request must carry a notice telling the model it is partial"
    )
    # The system prompt is preserved (it is trimmed only as the last resort).
    assert any(m.role == "system" for m in out)


def test_tool_catalogue_is_reduced_when_still_over_budget():
    """Escalation step 2: with a large tool list, only core tools survive."""
    msgs = [
        LLMMessage(role="system", content="S" * 100),
        LLMMessage(role="user", content="y" * 400),
        LLMMessage(role="assistant", content="z" * 400),
        LLMMessage(role="user", content="CURRENT-USER-MESSAGE"),
    ]
    tools = [{"name": "file_read", "description": "d" * 200,
              "parameters": {"type": "object", "properties": {}}}]
    tools += [{"name": f"tool_{i}", "description": "d" * 200,
               "parameters": {"type": "object", "properties": {}}} for i in range(22)]
    budget = 200  # tiny: forces the tool reduction

    out, kept_tools, report = fit_to_budget(
        msgs, tools, budget, core_tool_names=CORE_TOOL_NAMES,
    )

    assert report.tools_dropped > 0
    names = {t.get("name") for t in kept_tools}
    assert names <= set(CORE_TOOL_NAMES)
    assert "file_read" in names


def test_agent_prompt_budget_reads_max_prompt_tokens():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             max_prompt_tokens=3000))
    assert agent._prompt_budget() == 3000


def test_agent_prompt_budget_from_context_window_leaves_room():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             context_window=8192, max_tokens=4096))
    budget = agent._prompt_budget()
    assert budget is not None
    assert 0 < budget < 8192, "the reply must keep room inside the window"


def test_agent_prompt_budget_unset_is_none():
    assert _agent(LLMConfig(provider="openai", model="m", api_key="k")
                  )._prompt_budget() is None


def test_agent_fit_request_budget_trims_and_telemeters():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             max_prompt_tokens=1500))
    msgs = _long_history()
    fitted, tools, report = asyncio.run(
        agent._fit_request_budget(msgs, None, turn=1))
    assert report is not None and report.trimmed is True
    assert approx_prompt_tokens(fitted, tools) <= 1500
    assert "CURRENT-USER-MESSAGE" in [m.content for m in fitted]


def test_local_model_overflow_wording_is_recognised_and_the_window_learned():
    """The exact LM Studio rejection must drive compaction + window learning.

    Before this the phrase ``exceeds the available context size`` matched none
    of the markers, so a local model's oversized request was reported as a hard
    failure instead of being compacted and retried — and its window was never
    learned.
    """
    exc = RuntimeError(
        "request (8208 tokens) exceeds the available context size "
        "(8192 tokens), try increasing it"
    )
    assert is_context_length_error(exc) is True
    assert context_limit_of(exc) == 8192


# ===========================================================================
# ③ light tool mode
# ===========================================================================


_ALL_TOOL_NAMES = [
    "file_read", "file_write", "file_edit_replace", "multi_edit", "grep",
    "find", "code_search", "git", "terminal", "webfetch", "websearch",
    "history_search", "spawn_subagent", "subagent_status", "subagent_result",
    "browser", "computer_use", "write_todos",
]


def _tools() -> List[_FakeTool]:
    return [_FakeTool(n) for n in _ALL_TOOL_NAMES]


def test_default_mode_advertises_every_tool():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k"),
                   tools=_tools())
    schemas = agent._get_tool_schemas()
    assert len(schemas) == len(_ALL_TOOL_NAMES), (
        "default mode must not drop any tool (cloud behaviour unchanged)"
    )


def test_light_tools_flag_advertises_only_the_core_five():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             light_tools=True), tools=_tools())
    schemas = agent._get_tool_schemas()
    names = {s["name"] for s in schemas}
    assert names == set(CORE_TOOL_NAMES)
    assert len(schemas) == 5


def test_small_window_infers_light_mode():
    cfg = LLMConfig(provider="openai", model="m", api_key="k",
                    context_window=8192)
    assert is_light_mode(cfg) is True
    agent = _agent(cfg, tools=_tools())
    assert len(agent._get_tool_schemas()) == 5


def test_large_window_keeps_the_full_catalogue():
    cfg = LLMConfig(provider="openai", model="m", api_key="k",
                    context_window=200000)
    assert is_light_mode(cfg) is False
    agent = _agent(cfg, tools=_tools())
    assert len(agent._get_tool_schemas()) == len(_ALL_TOOL_NAMES)


def test_light_mode_falls_back_to_full_when_no_core_tool_is_wired():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             light_tools=True),
                   tools=[_FakeTool("webfetch"), _FakeTool("browser")])
    schemas = agent._get_tool_schemas()
    assert len(schemas) == 2, "never advertise an empty toolset"


# ===========================================================================
# ④ per-provider timeout
# ===========================================================================


def test_timeout_unset_keeps_the_historical_default():
    # LLMConfig.timeout defaults to 120; unset timeout_s must not change it.
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k"))
    assert agent._llm_timeout_s == 120.0


def test_timeout_zero_means_no_limit():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             timeout_s=0))
    assert agent._llm_timeout_s is None


def test_negative_timeout_also_means_no_limit():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             timeout_s=-1))
    assert agent._llm_timeout_s is None


def test_positive_timeout_is_used():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             timeout_s=600))
    assert agent._llm_timeout_s == 600.0


def test_timeout_label_is_safe_when_unlimited():
    agent = _agent(LLMConfig(provider="openai", model="m", api_key="k",
                             timeout_s=0))
    assert agent._llm_timeout_label == "no limit"


# ===========================================================================
# source-level guard (convention of tests/test_r37_ui_source.py)
# ===========================================================================


def _code(rel: str) -> str:
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    return "\n".join(line for line in src.splitlines()
                     if not line.strip().startswith("#"))


def test_provider_never_promotes_reasoning_into_the_answer():
    """Pin the rule at the source: an empty ``content`` must NOT be "rescued"
    by copying the reasoning channel into it.

    That rescue is exactly how the model's private monologue ("Let me build a
    script…") reached the user as the answer. An empty content stays empty and
    is *marked* (``reply_from_reasoning``), never overwritten with the thinking.
    """
    src = _code("kairos/llm/providers/openai_provider.py")
    assert "content_text = reasoning_text" not in src, (
        "the empty-content => reasoning-as-answer substitution is back"
    )
    assert "yield reasoning_accum" not in src, (
        "the streamed reasoning is being surfaced as the reply again"
    )
    # The event is still recorded, and both reasoning field spellings are read.
    assert "reply_from_reasoning = True" in src
    assert "NOT using the reasoning channel as the reply" in src
    assert '"reasoning_content"' in src and '"reasoning"' in src


def test_agent_records_the_reasoning_channel_reply_in_telemetry():
    src = _code("kairos/agents/base.py")
    assert "reply_from_reasoning" in src, (
        "the agent must record when a reply came from the reasoning channel"
    )
    assert "reason: \"reply_from_reasoning\"" in src.replace("'", '"') or \
        'reason": "reply_from_reasoning"' in src.replace("'", '"')
