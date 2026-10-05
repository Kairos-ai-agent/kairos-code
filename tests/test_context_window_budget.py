"""The request budget follows the model's window when anyone knows it.

``_max_tokens`` was a fixed 80000 unrelated to any model: for a smaller window
the provider rejected the request first and the recovery path halved a number
that had nothing to do with the real ceiling; for a larger window context was
thrown away for nothing. Compaction fires at 80% of whatever this returns, so
this is the number that decides whether a request ever gets built too big.
"""
from __future__ import annotations

import asyncio

import pytest

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage
from kairos.llm.errors import context_limit_of


class _Err(RuntimeError):
    pass


def _agent(**cfg_kwargs) -> KairosAgent:
    cfg = LLMConfig(provider="openai", model="m", api_key="k", **cfg_kwargs)
    return KairosAgent(agent_id="a1", name="coder", role="coder",
                       system_prompt="sys", llm_config=cfg,
                       message_bus=MessageBus(), tools=[])


# ============================================================ reading the number

@pytest.mark.parametrize("message,expected", [
    ("This model's maximum context length is 32768 tokens.", 32768),
    ("maximum context length is 65536 tokens", 65536),
    ("The context length of 131072 tokens was exceeded", 131072),
    ("model's max tokens: 200000", 200000),
])
def test_the_stated_window_is_read_out_of_the_rejection(message, expected):
    assert context_limit_of(_Err(message)) == expected


def test_no_number_means_no_learned_window():
    assert context_limit_of(_Err("prompt is too long")) is None
    assert context_limit_of(_Err("")) is None


def test_an_implausible_number_is_not_believed():
    """A per-request cap is not a window: guessing from it would shrink the
    budget to a fraction of what the model actually holds."""
    assert context_limit_of(_Err("max_tokens: 1024")) is None
    assert context_limit_of(_Err("maximum context length is 99999999999")) is None


# ============================================================ the budget itself

def test_no_known_window_keeps_the_previous_budget():
    assert _agent()._context_budget() == 80000


def test_a_known_window_caps_the_budget_and_leaves_room_for_the_answer():
    agent = _agent(context_window=32768)  # default output cap is 8192
    assert agent._context_budget() == 32768 - 8192


def test_a_window_bigger_than_the_budget_does_not_raise_it():
    assert _agent(context_window=1_000_000)._context_budget() == 80000


def test_the_environment_can_supply_the_window(monkeypatch):
    monkeypatch.setenv("KAIROS_CONTEXT_WINDOW", "16384")
    assert _agent()._context_budget() == 16384 - 8192


def test_a_tiny_window_still_leaves_the_floor():
    """Otherwise the budget would approach zero and nothing could be sent."""
    assert _agent(context_window=9000)._context_budget() == 8000


# ============================================================ learning from a rejection

def test_a_rejection_teaches_the_real_window():
    agent = _agent()
    assert agent._context_budget() == 80000

    asyncio.run(agent._recover_context_overflow(
        exc=_Err("This model's maximum context length is 32768 tokens.")))

    assert agent._context_window == 32768
    # The budget was also halved, and the smaller of the two wins.
    assert agent._context_budget() == min(40000, 32768 - 8192)


def test_a_rejection_without_a_number_changes_no_window():
    agent = _agent()
    asyncio.run(agent._recover_context_overflow(exc=_Err("prompt is too long")))
    assert agent._context_window is None
    assert agent._context_budget() == 40000  # halved, as before


# ============================================================ the effect that matters

def test_a_small_window_makes_the_agent_drop_old_turns():
    """The whole point: compact *before* the provider says no."""
    agent = _agent(context_window=12000)  # budget floors at 8000
    agent._memory = [LLMMessage(role="user", content="x" * 6000)] * 6
    agent._truncate_memory()
    assert len(agent._memory) < 6


def test_an_unknown_window_leaves_memory_alone_here():
    agent = _agent()
    agent._memory = [LLMMessage(role="user", content="x" * 6000)] * 6
    agent._truncate_memory()
    assert len(agent._memory) == 6
