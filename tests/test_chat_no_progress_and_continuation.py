"""The chat loop must not die in the investigation stage.

The user's complaint was concrete: the agent spent three turns in a row doing
read-only work and closed every one of them with "需要你确认" / "下一轮让我动手",
instead of acting on an instruction it had already been given. The code said why:

* ``Coder.MAX_CHAT_TURNS = 10`` (kairos/agents/roles/coder.py) and the chat loop
  ``for turn in range(self.MAX_CHAT_TURNS)`` (kairos/agents/base.py) breaks only
  on a turn that carries NO tool calls. Ten read-only turns exhaust the budget,
  the loop falls through to the wrap-up, and ``CHAT_WRAPUP_DIRECTIVE`` used to
  ask the model for "需要用户确认的地方" -- so the closing line became a request
  for approval.
* There was no continuation: nothing restarted the budget, so a chat that was
  still working simply ended.

This file pins the fix, one clause at a time:

* a run of read-only turns injects a no-progress nudge into the NEXT request,
  and the loop does not end by asking for approval;
* an exhausted budget continues on the SAME context, and the continuation count
  is capped;
* the continuation count is telemetered;
* the standing discipline block and the wrap-up directive say the new rules.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

from kairos.agents.agent_parts.chat import AgentChatMixin
from kairos.agents.agent_parts.discipline import WORK_DISCIPLINE_DIRECTIVE
from kairos.agents.base import (
    CHAT_WRAPUP_DIRECTIVE,
    KairosAgent,
    MAX_CHAT_CONTINUATIONS,
    NO_PROGRESS_NUDGE,
    NO_PROGRESS_NUDGE_TURNS,
)
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig, LLMResponse, ToolCall
from kairos.tools.base import ToolResult

#: Phrases that mean the loop gave the user back a request for permission
#: instead of a result. None of them may appear in a reply.
APPROVAL_MARKERS = ("需要你确认", "需要用户确认", "请确认", "等你确认", "让我动手")


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------


class _FakeTool:
    """Just enough tool for ``_get_tool_schemas`` to return a real list."""

    def __init__(self, name: str):
        self.name = name

    def to_schema(self) -> Dict[str, Any]:
        return {"name": self.name, "description": "",
                "parameters": {"type": "object", "properties": {}}}


class _ScriptedLLM:
    """Always asks for the SAME tool, never writes prose.

    That is the shape the complaint describes: the model keeps investigating and
    never converges, so the only way the run can end is the budget running out.
    ``wrapup`` optionally answers the tools-OFF closing call.
    """

    def __init__(self, tool_name: str = "file_read", wrapup: str = ""):
        self.tool_name = tool_name
        self.wrapup = wrapup
        #: list[(messages, tools)] — every request, in order.
        self.calls: List[Tuple[list, Any]] = []

    async def complete(self, messages, tools=None, **kw):
        self.calls.append((list(messages), tools))
        last = getattr(messages[-1], "content", "") or ""
        if last == CHAT_WRAPUP_DIRECTIVE and self.wrapup:
            return LLMResponse(content=self.wrapup, model="stub",
                               finish_reason="stop", usage={})
        return LLMResponse(
            content="", model="stub", finish_reason="tool_calls", usage={},
            tool_calls=[ToolCall(id="c1", name=self.tool_name,
                                 arguments={"path": "a.py"})],
        )

    async def close(self):
        pass


def _make_agent(llm, *, turns: int | None = None):
    bus = MessageBus()
    cfg = LLMConfig(provider="openai", model="stub-model", api_key="sk-test",
                    base_url="https://example.invalid/v1")
    agent = KairosAgent(
        agent_id="p1.coder", name="Coder", role="coder",
        system_prompt="ROLE PROMPT", llm_config=cfg, message_bus=bus,
        tools=[_FakeTool("file_read"), _FakeTool("file_write")],
    )
    if turns is not None:
        agent.MAX_CHAT_TURNS = turns
    agent._llm = llm
    dispatched: List[str] = []

    async def _fake_dispatch(tc):
        dispatched.append(tc.name)
        return ToolResult(success=True, output="ok")

    agent._dispatch_tool = _fake_dispatch
    return agent, bus, dispatched


def _is_wrapup(call) -> bool:
    """The tools-OFF closing call is the one ending in CHAT_WRAPUP_DIRECTIVE."""
    messages, _tools = call
    return bool(messages) and (
        (getattr(messages[-1], "content", "") or "") == CHAT_WRAPUP_DIRECTIVE)


def _loop_calls(llm: _ScriptedLLM) -> list:
    return [c for c in llm.calls if not _is_wrapup(c)]


def _has_nudge(call) -> bool:
    messages, _tools = call
    return any(getattr(m, "content", None) == NO_PROGRESS_NUDGE
               for m in messages)


# ---------------------------------------------------------------------------
# 1) no-progress nudge + no approval-shaped exit
# ---------------------------------------------------------------------------


def test_read_only_spin_gets_the_nudge_and_no_approval_exit():
    llm = _ScriptedLLM(tool_name="file_read")
    agent, bus, dispatched = _make_agent(llm)

    reply = asyncio.run(agent.chat("按你倾向的做，动手"))

    # The stub never wrote: every dispatched call was a read.
    assert dispatched and set(dispatched) == {"file_read"}

    loop_calls = _loop_calls(llm)
    assert len(loop_calls) > NO_PROGRESS_NUDGE_TURNS

    # The nudge first appears on the turn AFTER the streak hit the threshold,
    # as the newest user turn of that request.
    assert not _has_nudge(loop_calls[0])
    assert not _has_nudge(loop_calls[NO_PROGRESS_NUDGE_TURNS - 1])
    assert _has_nudge(loop_calls[NO_PROGRESS_NUDGE_TURNS])
    assert loop_calls[NO_PROGRESS_NUDGE_TURNS][0][-1].content == NO_PROGRESS_NUDGE
    # ...and it keeps riding every later turn while the spin continues.
    assert _has_nudge(loop_calls[-1])

    # The loop must NOT end by asking the user for permission.
    assert reply.strip()
    for marker in APPROVAL_MARKERS:
        assert marker not in reply, f"reply asks for approval: {marker!r}"
    # Instead it names the real state and never claims changes landed.
    assert "只读调查" in reply
    assert "改动都在工作目录里" not in reply

    # It was the reply the user actually sees (published as the bubble).
    events = asyncio.run(bus.recent(limit=400))
    chats = [m for m in events if m.topic == "agent.chat"]
    assert chats and chats[-1].content == reply


def test_nudge_clears_once_a_write_happens():
    """One side-effecting turn resets the streak, so no nudge is injected."""
    llm = _ScriptedLLM(tool_name="file_write")
    agent, _bus, dispatched = _make_agent(llm)

    asyncio.run(agent.chat("动手"))

    assert set(dispatched) == {"file_write"}
    assert not any(_has_nudge(c) for c in _loop_calls(llm))


# ---------------------------------------------------------------------------
# 2) turn exhaustion auto-continues, bounded
# ---------------------------------------------------------------------------


def test_turn_exhaustion_continues_and_stops_at_the_cap():
    llm = _ScriptedLLM(tool_name="file_read")
    # A small budget keeps the run instant; the CAP is what is under test, and
    # it is independent of the budget size.
    agent, _bus, _ = _make_agent(llm, turns=2)

    asyncio.run(agent.chat("动手"))

    loop_calls = _loop_calls(llm)
    budgets = MAX_CHAT_CONTINUATIONS + 1
    assert len(loop_calls) == 2 * budgets, len(loop_calls)
    # The cap is exactly what stopped it -- and it did stop.
    assert agent._chat_continuations == MAX_CHAT_CONTINUATIONS
    assert agent.status.value == "idle"
    assert agent.current_turn == 0


def test_continuation_keeps_the_context_it_already_had():
    llm = _ScriptedLLM(tool_name="file_read")
    agent, _bus, _ = _make_agent(llm, turns=2)

    asyncio.run(agent.chat("动手"))

    loop_calls = _loop_calls(llm)
    first, last = loop_calls[0][0], loop_calls[-1][0]
    # The request only ever grows: nothing is reset between budgets.
    assert len(last) > len(first)
    assert any("动手" in (getattr(m, "content", "") or "") for m in last)


def test_an_answered_turn_is_never_continued():
    """A real answer ends the call: continuations are for exhaustion only."""

    class _Answering:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools=None, **kw):
            self.calls += 1
            return LLMResponse(content="做完了，改了 a.py。", model="stub",
                               finish_reason="stop", usage={})

        async def close(self):
            pass

    llm = _Answering()
    agent, _bus, _ = _make_agent(llm, turns=2)

    reply = asyncio.run(agent.chat("动手"))

    assert reply == "做完了，改了 a.py。"
    assert agent._chat_continuations == 0
    assert llm.calls == 1


# ---------------------------------------------------------------------------
# 3) the continuation count is telemetered
# ---------------------------------------------------------------------------


def test_continuations_are_telemetered():
    llm = _ScriptedLLM(tool_name="file_read")
    agent, bus, _ = _make_agent(llm, turns=2)

    asyncio.run(agent.chat("动手"))

    events = asyncio.run(bus.recent(limit=600))
    cont = [m for m in events
            if m.topic == "agent.progress"
            and m.metadata.get("reason") == "chat_continuation"]
    assert len(cont) == MAX_CHAT_CONTINUATIONS
    assert [m.metadata["continuations"] for m in cont] == \
        list(range(1, MAX_CHAT_CONTINUATIONS + 1))
    assert all(m.metadata["max_continuations"] == MAX_CHAT_CONTINUATIONS
               for m in cont)
    # Telemetry stays off the visible thread (agent.progress is filtered out)...
    from kairos.core.persistence import Persistence
    assert "agent.progress" not in Persistence.CHAT_TOPICS
    # ...while the count is also stamped on the reply payload's metadata.
    chats = [m for m in events if m.topic == "agent.chat"]
    assert chats[-1].metadata["continuations"] == MAX_CHAT_CONTINUATIONS


# ---------------------------------------------------------------------------
# 4) the prompt hardening is pinned
# ---------------------------------------------------------------------------


def test_work_discipline_carries_the_act_now_rule():
    text = WORK_DISCIPLINE_DIRECTIVE
    assert "不再请求批准" in text
    assert "只读调查有预算" in text
    assert "必须开始写文件" in text
    assert "绝不以「需要你确认" in text


def test_wrapup_directive_no_longer_invites_approval():
    # The old item 3 asked for "需要用户确认的地方" -- it must be gone.
    assert "需要用户确认" not in CHAT_WRAPUP_DIRECTIVE
    # Instead the closing line is explicitly forbidden to ask for approval.
    assert "不要用" in CHAT_WRAPUP_DIRECTIVE
    assert "需要你确认" in CHAT_WRAPUP_DIRECTIVE
    assert "下一轮让我动手" in CHAT_WRAPUP_DIRECTIVE


def test_the_no_progress_nudge_is_pinned():
    assert "必须开始产生真实的副作用" in NO_PROGRESS_NUDGE
    assert "file_write" in NO_PROGRESS_NUDGE
    assert "不要请求批准" in NO_PROGRESS_NUDGE
    assert NO_PROGRESS_NUDGE_TURNS == 2


def test_the_new_rule_reaches_the_assembled_chat_prompt():
    class _Stub(AgentChatMixin):
        project_id = None

    prompt = _Stub()._build_chat_system_prompt()
    assert "不再请求批准" in prompt
    assert "只读调查有预算" in prompt
