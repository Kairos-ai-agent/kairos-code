"""The agent's reply must reach the UI whole.

``agent.response`` is what the chat page renders as the agent's answer, and
because the thread rehydrates from the stored rows, a reply that was clipped on
the way to the bus stays clipped after every refresh.  It used to be published
as ``interim[:2000]``, so any answer over 2000 characters reached the UI cut
mid-sentence.  Same for ``task.result``.

These tests fail on any future re-introduction of a small clip on either path.
"""
import pytest

from kairos.llm.base import LLMConfig, LLMResponse

# Comfortably over the old 2000-char clip, with a tail marker we can look for.
LONG_REPLY = "A" * 1500 + "\n\n" + "B" * 1500 + "\n\nEND-OF-ANSWER"


class _FakeLLM:
    """Returns one long, tool-call-free answer so the loop finishes in a turn."""

    config = LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test")

    async def complete(self, messages, tools=None, **kw):
        return LLMResponse(content=LONG_REPLY, model="m", tool_calls=None)

    async def stream(self, messages, tools=None, **kw):
        yield LONG_REPLY

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_long_reply_is_published_whole(monkeypatch):
    from kairos.agents.base import AgentTask
    from kairos.agents.roles.coder import Coder
    from kairos.core.message_bus import MessageBus

    published = []
    real_publish = MessageBus.publish

    async def spy(self, message, *args, **kwargs):
        published.append(message)
        return await real_publish(self, message, *args, **kwargs)

    monkeypatch.setattr(MessageBus, "publish", spy)

    bus = MessageBus()
    agent = Coder(
        agent_id="a1",
        llm_config=_FakeLLM.config,
        message_bus=bus,
        tools=[],
    )
    agent._llm = _FakeLLM()

    await agent.run(AgentTask(id="t1", title="t", description="d"))

    replies = [m for m in published if m.topic == "agent.response"]
    assert replies, "agent.response was never published"
    assert replies[-1].content == LONG_REPLY, (
        "agent.response was clipped on the way to the bus — the chat page "
        "renders this text as the agent's reply, so the user sees a "
        "half-sentence"
    )

    results = [m for m in published if m.topic == "task.result"]
    assert results, "task.result was never published"
    assert results[-1].content == LONG_REPLY, "task.result was clipped"
