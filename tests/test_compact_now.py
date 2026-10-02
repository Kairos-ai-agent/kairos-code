"""G4: compaction actually runs, is bounded, and can never kill the run.

The loop runner used to call ``kairos.compaction.maybe_compact()`` with a
signature that does not exist and then discard the result, so it raised
TypeError on every round and the ``except`` below it swallowed the evidence.
Compaction happens through the agent now (``compact_now``), which is also
where the LLM is available — and every layer of it is required to be
non-fatal, because a long-running loop must not be killed by its own
housekeeping.
"""
from __future__ import annotations

import asyncio

from kairos.agents.base import KairosAgent
from kairos.compaction import build_digest, maybe_compact
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse

SUMMARY = "SUMMARY: chose approach X; Y is still broken"


class _StubLLM:
    def __init__(self, content: str = SUMMARY, error: Exception = None):
        self._content = content
        self._error = error
        self.calls = 0

    async def complete(self, messages, tools=None, **kw):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return LLMResponse(content=self._content, model="m",
                           finish_reason="stop", usage={})

    async def close(self):
        pass


def _agent(llm, n_messages: int = 60) -> KairosAgent:
    cfg = LLMConfig(provider="openai", model="test-model", api_key="sk-test",
                    base_url="https://api.example.invalid/v1")
    agent = KairosAgent(agent_id="a1", name="coder", role="coder",
                        system_prompt="sys", llm_config=cfg,
                        message_bus=MessageBus(), tools=[])
    agent._llm = llm
    agent._memory = [
        LLMMessage(role="user" if i % 2 else "assistant",
                   content=f"turn {i} " + "w" * 300)
        for i in range(n_messages)
    ]
    return agent


# ---------------------------------------------------------------------------
# compact_now
# ---------------------------------------------------------------------------


def test_compact_now_bounds_memory_and_keeps_the_conclusions():
    agent = _agent(_StubLLM())

    changed = asyncio.run(agent.compact_now("test"))

    assert changed is True
    assert len(agent._memory) <= 16, "memory must be bounded after compaction"
    assert SUMMARY in agent._memory_summary
    # The summary is what the next request carries in place of the dropped turns.
    rendered = agent._build_messages()
    assert any(SUMMARY in (m.content or "") for m in rendered)


def test_compact_now_is_a_noop_for_a_short_session():
    agent = _agent(_StubLLM(), n_messages=3)

    assert asyncio.run(agent.compact_now()) is False


def test_compact_now_survives_a_failing_summarizer():
    """A provider outage costs the summary, not the session."""
    agent = _agent(_StubLLM(error=RuntimeError("provider down")), n_messages=60)

    result = asyncio.run(agent.compact_now())

    assert isinstance(result, bool)
    assert len(agent._memory) <= 16


# ---------------------------------------------------------------------------
# the structural digest
# ---------------------------------------------------------------------------


def _rounds(n: int, with_coder: bool = True) -> list:
    out = []
    for i in range(n):
        entry = {
            "round": i,
            "review": {"score": 50 + i, "approve": i == n - 1,
                       "issues": [{"severity": "high", "category": "correctness"}]},
        }
        if with_coder:
            entry["coder"] = f"round {i} changed thing {i} " + "z" * 400
        out.append(entry)
    return out


def test_digest_keeps_the_folded_rounds_own_conclusions():
    digest = build_digest(_rounds(10)[:7])

    assert digest.work_log, "what the folded rounds did must survive the fold"
    assert "Recent work folded in" in digest.summary
    assert "thing 6" in digest.summary, "the newest folded round is the one that matters"


def test_digest_still_works_when_rounds_have_no_coder_output():
    digest = build_digest(_rounds(5, with_coder=False))

    assert digest.rounds == 5
    assert digest.work_log == []
    assert "Compacted 5 round(s)" in digest.summary


def test_maybe_compact_folds_normally():
    out = maybe_compact(_rounds(15), threshold=12, keep_recent=5)

    assert out[0]["type"] == "compaction"
    assert len(out) == 6, "one digest + the five kept rounds"
    assert out[0]["rounds"] == 10


def test_maybe_compact_never_raises_on_bad_input():
    """A fold that fails returns the history unchanged — that is the contract."""
    history = [{"round": object()} for _ in range(15)]

    out = maybe_compact(history, threshold=3, keep_recent=2)

    assert isinstance(out, list)
    assert len(out) == len(history)
