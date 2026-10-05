"""What the running summary keeps, and what it says about what it dropped.

A summary is what survives compaction, so the cost of leaving a category out is
that it is gone from the prompt for good — and the cost of not saying how far
back it reaches is that the next run cannot tell whether the detail it wants is
inside the summary or older than it.
"""
from __future__ import annotations

import asyncio

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse


class _LLM:
    def __init__(self, reply: str = "## Summary\n- did the thing"):
        self.prompts: list[str] = []
        self.reply = reply

    async def complete(self, messages, **kwargs) -> LLMResponse:
        self.prompts.append(messages[0].content)
        return LLMResponse(content=self.reply, model="m",
                           finish_reason="stop", usage={})


def _agent(llm: _LLM) -> KairosAgent:
    cfg = LLMConfig(provider="openai", model="m", api_key="k")
    agent = KairosAgent(agent_id="a1", name="coder", role="coder",
                        system_prompt="sys", llm_config=cfg,
                        message_bus=MessageBus(), tools=[])
    agent._llm = llm
    agent._memory = [LLMMessage(role="user", content="x" * 200)] * 8
    return agent


def test_the_prompt_asks_for_what_a_later_turn_would_otherwise_redo():
    llm = _LLM()
    agent = _agent(llm)

    asyncio.run(agent._maybe_summarize_memory(10, force=True))

    prompt = llm.prompts[0]
    for wanted in ("Decisions rejected", "Files and paths", "Commands and tools",
                   "Constraints and instructions", "State of the work",
                   "would otherwise redo"):
        assert wanted in prompt, wanted


def test_the_prompt_also_says_what_to_drop():
    """Without this the budget goes to restated tool output."""
    llm = _LLM()
    agent = _agent(llm)

    asyncio.run(agent._maybe_summarize_memory(10, force=True))

    assert "Drop:" in llm.prompts[0]


def test_the_summary_says_how_far_back_it_reaches():
    llm = _LLM()
    agent = _agent(llm)

    asyncio.run(agent._maybe_summarize_memory(10, force=True))

    summary = agent._memory_summary
    assert summary.startswith("## Summary")          # the model's text is kept
    assert "compressed up to turn 10" in summary
    assert "4 messages" in summary                    # 8 kept in memory, 4 folded


def test_a_later_pass_folds_into_the_previous_summary():
    llm = _LLM()
    agent = _agent(llm)

    asyncio.run(agent._maybe_summarize_memory(10, force=True))
    asyncio.run(agent._maybe_summarize_memory(20, force=True))

    assert "Existing summary" in llm.prompts[1]
    assert "compressed up to turn 20" in agent._memory_summary
