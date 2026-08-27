"""Tests for kairos.cost (Round 14 cost tracking)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from kairos.cost import (
    CostEntry,
    clear_buffer,
    cost_by_model,
    cost_summary,
    get_buffer,
    install_cost_callbacks,
    litellm_cost_callback,
    set_log_path,
    total_cost,
)


@pytest.fixture(autouse=True)
def _reset_cost():
    """Reset the in-memory cost buffer + log path between tests."""
    clear_buffer()
    set_log_path(Path(os.environ.get("TEMP", "/tmp")) / "kairos-test-cost.jsonl")
    yield
    clear_buffer()


def _make_litellm_response(model: str, prompt_tokens: int,
                           completion_tokens: int, cost_usd: float):
    """Build a fake litellm ModelResponse with cost in _hidden_params."""
    resp = MagicMock()
    resp.usage = MagicMock(prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=prompt_tokens + completion_tokens)
    resp.model = model
    # Cost is on _hidden_params for litellm >= 1.40
    resp._hidden_params = {"response_cost": cost_usd}
    return resp


# ---------------------------------------------------------------------------
# litellm_cost_callback
# ---------------------------------------------------------------------------


def test_callback_records_basic_entry():
    resp = _make_litellm_response("gpt-4o", 100, 50, 0.001)
    litellm_cost_callback(
        kwargs={"model": "gpt-4o"},
        completion_response=resp,
        start_time=time.time() - 0.5,
        end_time=time.time(),
    )
    entries = get_buffer()
    assert len(entries) == 1
    e = entries[0]
    assert e.model == "gpt-4o"
    # No "/" in the model name → provider is inferred from the
    # model prefix (gpt-* → openai)
    assert e.provider == "openai"
    assert e.prompt_tokens == 100
    assert e.completion_tokens == 50
    assert e.cost_usd == 0.001
    assert e.duration_ms >= 500


def test_callback_extracts_provider_from_slash_model():
    """For 'anthropic/claude-3-5-sonnet', provider is 'anthropic'."""
    resp = _make_litellm_response("anthropic/claude-3-5-sonnet-20241022", 200, 100, 0.003)
    litellm_cost_callback(
        kwargs={"model": "anthropic/claude-3-5-sonnet-20241022"},
        completion_response=resp, start_time=0, end_time=1.0,
    )
    e = get_buffer()[0]
    assert e.provider == "anthropic"
    assert "claude" in e.model


def test_callback_handles_missing_cost():
    """Older litellm versions don't set _hidden_params['response_cost']."""
    resp = MagicMock()
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    resp._hidden_params = {}
    litellm_cost_callback(
        kwargs={"model": "m"}, completion_response=resp,
        start_time=0, end_time=0.5,
    )
    e = get_buffer()[0]
    assert e.cost_usd == 0.0  # graceful default
    assert e.prompt_tokens == 10


def test_callback_never_raises_on_garbage_response():
    """A bad response shape must not break the LLM call."""
    bad = object()  # no attributes
    litellm_cost_callback(
        kwargs={"model": "m"}, completion_response=bad,
        start_time=0, end_time=1.0,
    )
    # Either an entry was recorded or no entry was — but no exception
    assert isinstance(get_buffer(), list)


def test_callback_writes_to_jsonl_log(tmp_path: Path):
    """The on-disk JSONL sink receives one line per call."""
    log = tmp_path / "cost.jsonl"
    set_log_path(log)
    resp = _make_litellm_response("m", 5, 3, 0.0001)
    litellm_cost_callback(
        kwargs={"model": "m"}, completion_response=resp,
        start_time=0, end_time=0.1,
    )
    assert log.exists()
    lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["model"] == "m"
    assert entry["cost_usd"] == 0.0001


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_cost_by_model_aggregates():
    """Multiple calls to the same model aggregate into one entry."""
    for i in range(3):
        resp = _make_litellm_response("gpt-4o", 100, 50, 0.001)
        litellm_cost_callback(
            kwargs={"model": "gpt-4o"}, completion_response=resp,
            start_time=0, end_time=0.5,
        )
    for i in range(2):
        resp = _make_litellm_response("claude-3-5-sonnet", 200, 100, 0.003)
        litellm_cost_callback(
            kwargs={"model": "claude-3-5-sonnet"}, completion_response=resp,
            start_time=0, end_time=0.5,
        )
    agg = cost_by_model()
    assert "gpt-4o" in agg
    assert "claude-3-5-sonnet" in agg
    assert agg["gpt-4o"]["calls"] == 3
    assert agg["gpt-4o"]["cost_usd"] == 0.003
    assert agg["claude-3-5-sonnet"]["calls"] == 2
    assert agg["claude-3-5-sonnet"]["cost_usd"] == 0.006


def test_cost_summary_includes_totals():
    for i in range(5):
        resp = _make_litellm_response("m", 1, 1, 0.01)
        litellm_cost_callback(
            kwargs={"model": "m"}, completion_response=resp,
            start_time=0, end_time=0.5,
        )
    summary = cost_summary()
    assert summary["calls"] == 5
    assert summary["cost_usd"] == 0.05
    assert "m" in summary["models"]


def test_cost_summary_empty_buffer():
    """An empty buffer returns a zero-cost summary."""
    summary = cost_summary()
    assert summary["calls"] == 0
    assert summary["cost_usd"] == 0.0
    assert summary["models"] == {}


def test_total_cost():
    """total_cost() sums the in-memory buffer."""
    assert total_cost() == 0.0
    for i in range(4):
        resp = _make_litellm_response("m", 1, 1, 0.005)
        litellm_cost_callback(
            kwargs={"model": "m"}, completion_response=resp,
            start_time=0, end_time=0.1,
        )
    assert total_cost() == pytest.approx(0.02)


def test_clear_buffer():
    """clear_buffer() resets the in-memory state."""
    resp = _make_litellm_response("m", 1, 1, 0.01)
    litellm_cost_callback(
        kwargs={"model": "m"}, completion_response=resp,
        start_time=0, end_time=0.1,
    )
    assert len(get_buffer()) == 1
    clear_buffer()
    assert len(get_buffer()) == 0


# ---------------------------------------------------------------------------
# install_cost_callbacks
# ---------------------------------------------------------------------------


def test_install_cost_callbacks_returns_int():
    """The install function returns 0 (no litellm) or 1 (installed)."""
    n = install_cost_callbacks()
    assert n in (0, 1)
