"""Tests for the LiteLLM provider.

We mock the litellm package to avoid requiring it as a runtime
dependency for the test suite. The point is to prove the
provider correctly translates our LLMMessage / LLMConfig shapes
into litellm's API and the response back into LLMResponse.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.providers import litellm_provider as lp


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_litellm_provider_is_registered():
    from kairos.llm.provider_registry import ProviderRegistry
    assert "litellm" in ProviderRegistry.list_providers()


# ---------------------------------------------------------------------------
# Message / tool coercion
# ---------------------------------------------------------------------------


def test_coerce_messages_basic():
    msgs = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="hi"),
    ]
    out = lp._coerce_messages(msgs)
    assert out == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]


def test_coerce_messages_with_tool_call_id():
    msgs = [LLMMessage(role="tool", content="result", tool_call_id="c1")]
    out = lp._coerce_messages(msgs)
    assert out == [{"role": "tool", "content": "result", "tool_call_id": "c1"}]


def test_coerce_messages_with_tool_calls_dict_arguments():
    msgs = [LLMMessage(
        role="assistant", content="",
        tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "rust"})],
    )]
    out = lp._coerce_messages(msgs)
    assert out[0]["tool_calls"] == [{
        "id": "c1", "type": "function",
        "function": {"name": "search", "arguments": '{"q": "rust"}'},
    }]


def test_coerce_messages_with_tool_calls_string_arguments():
    msgs = [LLMMessage(
        role="assistant", content="",
        tool_calls=[ToolCall(id="c1", name="search", arguments='{"q":"rust"}')],
    )]
    out = lp._coerce_messages(msgs)
    # String arguments are passed through verbatim
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"q":"rust"}'


# ---------------------------------------------------------------------------
# Response coercion
# ---------------------------------------------------------------------------


def test_to_response_text_only():
    """A response with just text content maps to LLMResponse."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "hello"
    resp.choices[0].message.tool_calls = None
    resp.choices[0].finish_reason = "stop"
    resp.model = "gpt-4o"
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    out = lp._to_response(resp)
    assert out.content == "hello"
    assert out.finish_reason == "stop"
    assert out.model == "gpt-4o"
    assert out.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert out.tool_calls is None


def test_to_response_with_tool_calls():
    """A response with tool_calls maps to LLMResponse with parsed args."""
    tc = MagicMock()
    tc.id = "c1"
    tc.function = {"name": "search", "arguments": '{"q": "rust"}'}

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = ""
    resp.choices[0].message.tool_calls = [tc]
    resp.choices[0].finish_reason = "tool_calls"
    resp.model = "gpt-4o"
    resp.usage = None

    out = lp._to_response(resp)
    assert out.finish_reason == "tool_calls"
    assert out.tool_calls is not None
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "search"
    # JSON string args are parsed into dict
    assert out.tool_calls[0].arguments == {"q": "rust"}


def test_to_response_handles_garbled_args():
    """Non-JSON string args are passed through verbatim (caller can fix)."""
    tc = MagicMock()
    tc.id = "c1"
    tc.function = {"name": "search", "arguments": "{garbled"}

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = ""
    resp.choices[0].message.tool_calls = [tc]
    resp.choices[0].finish_reason = "tool_calls"
    resp.model = "m"
    resp.usage = None

    out = lp._to_response(resp)
    assert out.tool_calls[0].arguments == "{garbled"


def test_to_response_garbled_choice_returns_empty():
    """Malformed responses don't crash; they yield an empty result."""
    resp = MagicMock()
    resp.choices = []  # empty
    out = lp._to_response(resp)
    assert out.content == ""
    assert out.finish_reason == "error"


# ---------------------------------------------------------------------------
# Provider construction (no real network)
# ---------------------------------------------------------------------------


def test_provider_name_and_model():
    """The provider exposes the standard name/model props."""
    # Patch _ensure_litellm so the import isn't required.
    with patch.object(lp, "_ensure_litellm", lambda: None):
        cfg = LLMConfig(provider="litellm", model="gpt-4o", api_key="sk-test")
        p = lp.LiteLLMProvider(cfg)
    assert p.name == "litellm"
    assert p.model == "gpt-4o"


def test_provider_uses_base_url_for_compatible_backends():
    """base_url flows through to the litellm call (vllm / ollama proxy)."""
    with patch.object(lp, "_ensure_litellm", lambda: None):
        cfg = LLMConfig(
            provider="litellm", model="vllm/my-model",
            base_url="http://gpu-box:8000/v1", api_key="fake",
        )
        p = lp.LiteLLMProvider(cfg)
    assert p._base_url == "http://gpu-box:8000/v1"
