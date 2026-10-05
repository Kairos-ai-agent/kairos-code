"""Team workers must not share the project's Coder.

Multiple workers used to call ``project.coder.run(...)`` on one instance. Every
turn a worker produced was appended to that agent's own ``_memory``, and every
worker held its ``_lock`` for the whole run — so workers leaked their entire
conversation into each other *and into the project's Coder*, which then carried
all of it into its own later turns, and they ran strictly one at a time.

``KairosAgent.fork()`` already existed for exactly this. The API path simply
wasn't using it.
"""
from __future__ import annotations

import asyncio
from typing import List

from api.routes.teams import _make_worker_fn
from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMResponse, LLMMessage
from kairos.teams import TeamTask


class _StubCtx:
    team_id = "t1"


class _StubWorker:
    def __init__(self, sink: List[str]):
        self._sink = sink

    async def run(self, agent_task):
        self._sink.append(agent_task.id)
        return f"done:{agent_task.id}"


class _StubCoder:
    """Deliberately has no ``run``: calling one would be an AttributeError."""

    def __init__(self):
        self.forks = 0
        self.ran: List[str] = []

    def fork(self):
        self.forks += 1
        return _StubWorker(self.ran)


class _Project:
    def __init__(self, coder):
        self.coder = coder


def _task(tid: str) -> TeamTask:
    return TeamTask(id=tid, title=f"task-{tid}", description="do the thing")


def test_each_worker_gets_its_own_agent():
    coder = _StubCoder()
    worker_fn = _make_worker_fn(_Project(coder))

    async def go():
        return await asyncio.gather(
            worker_fn(_task("a"), _StubCtx()),
            worker_fn(_task("b"), _StubCtx()),
        )

    out = asyncio.run(go())

    assert coder.forks == 2, "two workers shared one agent instead of forking"
    assert coder.ran == ["t1.a", "t1.b"], "each fork must get its own task id"
    assert out == ["done:t1.a", "done:t1.b"]


class _StubLLM:
    async def complete(self, messages, tools=None, **kw):
        return LLMResponse(content="", model="stub")

    async def close(self):
        pass


def _real_agent() -> KairosAgent:
    cfg = LLMConfig(provider="openai", model="stub-model", api_key="sk-test")
    agent = KairosAgent(
        agent_id="a1", name="coder", role="coder", system_prompt="sys",
        llm_config=cfg, message_bus=MessageBus(), tools=[],
    )
    agent._llm = _StubLLM()
    return agent


def test_fork_isolates_memory_and_lock():
    """The property ``_make_worker_fn`` now depends on — pinned directly."""
    agent = _real_agent()
    agent._memory.append(LLMMessage(role="user", content="baseline"))

    first, second = agent.fork(), agent.fork()

    assert first._memory == agent._memory
    assert second._memory == agent._memory
    assert len(first._memory) == 1

    # A worker's history must not reach the project's Coder or its siblings.
    first._memory.append(LLMMessage(role="assistant", content="worker one only"))
    assert len(agent._memory) == 1, "a worker wrote into the project's Coder"
    assert len(second._memory) == 1, "workers shared one history"

    assert first._lock is not second._lock
    assert first._lock is not agent._lock
