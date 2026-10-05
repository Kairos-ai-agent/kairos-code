"""A summary that cannot succeed is not retried forever.

Every attempt is a whole LLM call (up to the per-call timeout) and the
transcript only grows, so the second attempt is the same request as the first —
same outcome, same price. After a few failures the agent stops asking and lets
the retention window do the bounding instead.
"""
from __future__ import annotations

import asyncio

from kairos.agents.base import KairosAgent
from kairos.context_governor import MAX_SUMMARIZE_FAILURES
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse


class _LLM:
    """Stub provider: counts calls, fails while ``fail`` is set."""

    def __init__(self, fail: bool = True):
        self.calls = 0
        self.fail = fail

    async def complete(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider rejected the summary request")
        return LLMResponse(content="a terse summary", model="m",
                           finish_reason="stop", usage={})


def _agent(llm: _LLM) -> KairosAgent:
    cfg = LLMConfig(provider="openai", model="m", api_key="k")
    agent = KairosAgent(agent_id="a1", name="coder", role="coder",
                        system_prompt="sys", llm_config=cfg,
                        message_bus=MessageBus(), tools=[])
    agent._llm = llm
    agent._memory = [LLMMessage(role="user", content="x" * 200)] * 8
    return agent


def test_it_stops_asking_after_the_limit():
    llm = _LLM()
    agent = _agent(llm)

    for _ in range(MAX_SUMMARIZE_FAILURES + 4):
        asyncio.run(agent._maybe_summarize_memory(10, force=True))

    assert llm.calls == MAX_SUMMARIZE_FAILURES


def test_a_success_clears_the_run_of_failures():
    llm = _LLM()
    agent = _agent(llm)

    asyncio.run(agent._maybe_summarize_memory(10, force=True))
    assert llm.calls == 1
    assert agent._summarize_failures == 1

    llm.fail = False
    asyncio.run(agent._maybe_summarize_memory(10, force=True))

    assert agent._summarize_failures == 0
    assert agent._memory_summary == "a terse summary"


def test_giving_up_is_quiet():
    """Compaction that can kill the run is a bug, so nothing escapes."""
    llm = _LLM()
    agent = _agent(llm)

    for _ in range(MAX_SUMMARIZE_FAILURES + 2):
        asyncio.run(agent._maybe_summarize_memory(10, force=True))


def test_a_forced_compaction_also_respects_the_limit():
    """`compact_now` runs on every few rounds: it must not pay for a doomed
    summary each time either."""
    llm = _LLM()
    agent = _agent(llm)

    for _ in range(MAX_SUMMARIZE_FAILURES + 3):
        asyncio.run(agent.compact_now("round boundary"))

    assert llm.calls <= MAX_SUMMARIZE_FAILURES
