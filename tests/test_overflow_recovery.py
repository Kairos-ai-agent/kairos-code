"""A provider that says "too long" must buy a retry, not an error bubble.

Both the run loop and the chat path used to treat every non-timeout provider
failure as fatal — a context-window rejection ended the turn with
"Error: Error code: 400 …". The provider has just told us exactly what was
wrong, so the correct reaction is to compact and ask again.

The stub below enforces the real contract rather than an absolute size: the
first request is rejected as oversized, and any later request that is *not*
strictly smaller is rejected too. These tests therefore cannot pass unless
recovery genuinely shrank the request.
"""
from __future__ import annotations

import asyncio

from kairos.agents.base import AgentTask, KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse, ToolCall

OVERFLOW = RuntimeError(
    "400 - This model's maximum context length is 65536 tokens. "
    "However, you requested 90000 tokens."
)


class _OversizeRejectingLLM:
    """Rejects the first request, and any later one that is not smaller."""

    def __init__(self, content: str = "recovered", reject_first: bool = True):
        self._content = content
        self._reject_first = reject_first
        self.request_chars: list[int] = []
        self.last_messages = None

    async def complete(self, messages, tools=None, **kw):
        size = sum(len(m.content or "") for m in messages)
        self.request_chars.append(size)
        self.last_messages = list(messages)
        if self._reject_first and len(self.request_chars) == 1:
            raise OVERFLOW
        if len(self.request_chars) > 1 and size >= self.request_chars[0]:
            raise OVERFLOW
        return LLMResponse(content=self._content, model="m",
                           finish_reason="stop", usage={})

    def stream(self, *a, **kw):
        # The run loop falls back to complete() when stream is unavailable.
        raise NotImplementedError("no streaming in this stub")

    async def close(self):
        pass


def _make_agent(llm, memory=None) -> KairosAgent:
    cfg = LLMConfig(provider="openai", model="test-model", api_key="sk-test",
                    base_url="https://api.example.invalid/v1")
    agent = KairosAgent(agent_id="a1", name="coder", role="coder",
                        system_prompt="sys", llm_config=cfg,
                        message_bus=MessageBus(), tools=[])
    agent._llm = llm
    if memory is not None:
        agent._memory = list(memory)
    return agent


def _long_memory(n: int = 24) -> list:
    """A conversation long enough that dropping turns actually frees space.

    The assistant/tool pairs are declared properly (a ``tool_calls`` entry
    answered by a ``role="tool"`` message): ``_sanitize_memory`` drops or
    repairs anything that does not pair up, so a memory built without them
    would arrive at the provider empty and prove nothing.
    """
    msgs = []
    for i in range(n):
        msgs.append(LLMMessage(role="user", content=f"ask {i} " + "q" * 200))
        msgs.append(LLMMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(id=f"t{i}", name="read_file", arguments={})],
        ))
        msgs.append(LLMMessage(role="tool", content="t" * 2000,
                               tool_call_id=f"t{i}", name="read_file"))
    return msgs


def _raiser(exc):
    async def _complete(messages, tools=None, **kw):
        raise exc
    return _complete


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


def test_chat_recovers_from_context_overflow():
    llm = _OversizeRejectingLLM()
    agent = _make_agent(llm, memory=_long_memory())

    reply = asyncio.run(agent.chat("hello"))

    assert reply == "recovered", "the turn must complete, not report an error"
    assert len(llm.request_chars) >= 2, "the rejection must be retried"
    assert llm.request_chars[-1] < llm.request_chars[0], \
        "the retry has to be strictly smaller"


def test_overflow_recovery_lowers_the_budget_for_the_rest_of_the_session():
    llm = _OversizeRejectingLLM()
    agent = _make_agent(llm, memory=_long_memory())
    before = agent._max_tokens

    asyncio.run(agent.chat("hello"))

    assert agent._max_tokens == before // 2


def test_old_tool_bodies_are_elided_before_the_first_request():
    """G2 runs on the way out, every turn — not only after a rejection."""
    llm = _OversizeRejectingLLM(reject_first=False)
    agent = _make_agent(llm, memory=_long_memory())

    asyncio.run(agent.chat("hello"))

    bodies = [m.content for m in llm.last_messages if m.role == "tool"]
    assert any(b.startswith("[read_file output elided") for b in bodies), \
        "old tool bodies should have been stubbed before sending"
    # …while the session still holds every byte.
    stored = [m.content for m in agent._memory if m.role == "tool"]
    assert any(len(b) == 2000 for b in stored)


def test_non_overflow_provider_errors_still_surface():
    """Recovery must not swallow real failures into a silent retry."""
    llm = _OversizeRejectingLLM()
    llm.complete = _raiser(RuntimeError("Connection reset by peer"))
    agent = _make_agent(llm, memory=_long_memory())

    try:
        asyncio.run(agent.chat("hello"))
    except RuntimeError as exc:
        assert "Connection reset" in str(exc)
    else:  # pragma: no cover - the assertion below is the point
        raise AssertionError("a non-overflow error must propagate")


def test_chat_timeout_path_is_unchanged():
    llm = _OversizeRejectingLLM()
    llm.complete = _raiser(TimeoutError())
    agent = _make_agent(llm)

    reply = asyncio.run(agent.chat("hello"))

    assert "timed out" in reply


# ---------------------------------------------------------------------------
# run loop
# ---------------------------------------------------------------------------


def test_run_loop_recovers_from_context_overflow():
    llm = _OversizeRejectingLLM()
    agent = _make_agent(llm, memory=_long_memory())
    task = AgentTask(id="t1", title="do the thing", description="please")

    result = asyncio.run(agent.run(task))

    assert result.strip() == "recovered"
    assert task.status != "failed"
    assert llm.request_chars[-1] < llm.request_chars[0]


def test_run_loop_completes_normally_when_nothing_overflows():
    llm = _OversizeRejectingLLM(reject_first=False)
    agent = _make_agent(llm)
    task = AgentTask(id="t2", title="x", description="y")

    result = asyncio.run(agent.run(task))

    assert result == "recovered"
