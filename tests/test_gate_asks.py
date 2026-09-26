"""The gate's ASK, answered by a person.

`kairos/sentinel.py` has always said what an ASK means once an approval channel
exists. These tests are that sentence turned into behaviour: with a channel the
call waits, the answer decides, and no answer means no.
"""
from __future__ import annotations

import asyncio

import pytest

from kairos import approvals
from kairos.agents.base import KairosAgent
from kairos.sentinel import Decision, Sentinel, SentinelAudit
from kairos.tools.base import ToolResult


@pytest.fixture
def sentinel(tmp_path, monkeypatch):
    # Audit and standing rules must never land in the user's real directories.
    monkeypatch.setenv("KAIROS_SENTINEL_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path / "data"))
    s = Sentinel(audit=SentinelAudit(directory=tmp_path / "audit"))
    s.reload()
    return s


@pytest.fixture
def channel():
    ch = approvals.ApprovalChannel(timeout_s=5)
    approvals.set_channel(ch)
    try:
        yield ch
    finally:
        approvals.set_channel(None)


async def _ask_then(sentinel, channel, answer, *, remember=False, tool="file_write",
                    args=None):
    """Start a gated call, wait for the question, answer it, return the ruling."""
    ch = channel
    task = asyncio.ensure_future(
        sentinel.authorize_async(tool, args or {"path": "a.py", "content": "x"}))
    for _ in range(200):                      # the request is published on the loop
        await asyncio.sleep(0.01)
        if ch.pending():
            break
    pending = ch.pending()
    assert pending, "the gate should have asked"
    assert ch.resolve(pending[0]["id"], answer, remember=remember) is True
    return await task


# ---------------------------------------------------------------------------
# the precondition, so the rest of the file cannot pass vacuously
# ---------------------------------------------------------------------------

def test_the_ladder_asks_about_a_write(sentinel):
    """If this ever stops being ASK, every test below becomes a tautology."""
    decision, reason = sentinel._ladder("file_write", "a.py")
    assert decision == Decision.ASK, reason


# ---------------------------------------------------------------------------
# with nobody to ask
# ---------------------------------------------------------------------------

def test_without_a_channel_the_gate_behaves_as_before(sentinel):
    approvals.set_channel(None)
    ruling = asyncio.run(
        sentinel.authorize_async("file_write", {"path": "a.py", "content": "x"}))
    assert ruling.decision == "allow"
    assert ruling.rule == "default-allow", "unchanged from the sync path"


# ---------------------------------------------------------------------------
# with someone to ask
# ---------------------------------------------------------------------------

def test_an_approved_call_allows(sentinel, channel):
    ruling = asyncio.run(_ask_then(sentinel, channel, True))
    assert ruling.decision == "allow"
    assert ruling.rule == "approved-by-user"
    assert "approved" in ruling.reason


def test_the_question_names_the_tool_and_the_resource(sentinel, channel):
    async def main():
        task = asyncio.ensure_future(sentinel.authorize_async(
            "file_write", {"path": "a.py", "content": "x"}))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if channel.pending():
                break
        asked = channel.pending()[0]
        channel.resolve(asked["id"], True)
        return asked, await task

    asked, ruling = asyncio.run(main())
    assert asked["tool"] == "file_write"
    assert asked["resource"], "the resource tells the user what is about to happen"
    assert asked["status"] == "pending"
    assert ruling.decision == "allow"


def test_a_denied_call_is_refused(sentinel, channel):
    ruling = asyncio.run(_ask_then(sentinel, channel, False))
    assert ruling.decision == "deny"
    assert ruling.denied is True
    assert ruling.rule == "approval-denied"
    assert "did not approve" in ruling.reason


def test_a_silent_user_refuses(sentinel):
    """A question nobody answers must not become a permission."""
    ch = approvals.ApprovalChannel(timeout_s=0.05)
    approvals.set_channel(ch)
    try:
        ruling = asyncio.run(sentinel.authorize_async(
            "file_write", {"path": "a.py", "content": "x"}))
    finally:
        approvals.set_channel(None)
    assert ruling.decision == "deny"
    assert ruling.rule == "approval-timeout"
    assert ch.history()[-1]["status"] == "timeout", "and it is on the record"


def test_remember_records_the_standing_rule(sentinel, channel, monkeypatch):
    import kairos.sentinel as sentinel_module

    recorded = []
    monkeypatch.setattr(sentinel_module, "write_allow_rule",
                        lambda tool, pattern="*", decision="allow": recorded.append(
                            (tool, pattern)))

    asyncio.run(_ask_then(sentinel, channel, True, remember=True))
    assert recorded == [("file_write", "*")], "remember means: stop asking about this tool"


def test_without_remember_no_rule_is_written(sentinel, channel, monkeypatch):
    import kairos.sentinel as sentinel_module

    recorded = []
    monkeypatch.setattr(sentinel_module, "write_allow_rule",
                        lambda tool, pattern="*", decision="allow": recorded.append(
                            (tool, pattern)))
    asyncio.run(_ask_then(sentinel, channel, True, remember=False))
    assert recorded == []


def test_the_answered_decision_lands_in_the_audit_log(sentinel, channel):
    asyncio.run(_ask_then(sentinel, channel, True))
    entries = sentinel.audit.read(limit=20)
    assert any(e.get("rule") == "approved-by-user" for e in entries), entries


def test_a_denied_verdict_is_never_asked(sentinel, channel, monkeypatch):
    """Only ASK is a question. A deny is already an answer."""
    from dataclasses import replace

    denied = replace(sentinel.authorize("file_write",
                                        {"path": "a.py", "content": "x"}),
                     decision="deny", rule="test-deny", reason="not allowed")
    monkeypatch.setattr(sentinel, "authorize", lambda *a, **k: denied)

    ruling = asyncio.run(sentinel.authorize_async(
        "file_write", {"path": "a.py", "content": "x"}))
    assert ruling.denied is True
    assert channel.pending() == [], "a deny must not turn into a question"


# ---------------------------------------------------------------------------
# the call site
# ---------------------------------------------------------------------------

class _ToolCall:
    def __init__(self, name, arguments=None):
        self.name = name
        self.arguments = arguments or {}


class _CountingGate:
    """A sentinel double that only implements the async gate."""

    def __init__(self, ruling):
        self.ruling = ruling
        self.calls = 0

    async def authorize_async(self, name, args, **kwargs):
        self.calls += 1
        return self.ruling

    def observe_result(self, name, taint, tool):
        return None


class _Tool:
    name = "terminal"

    def __init__(self):
        self.ran = False

    async def execute(self, **kwargs):
        self.ran = True
        return ToolResult(success=True, output="ok")


def test_the_tool_dispatch_uses_the_question_asking_gate(sentinel, channel):
    """The whole point: the tool call goes through the gate that can ask."""
    ruling = sentinel.authorize("file_read", {"path": "a.py"})
    gate = _CountingGate(ruling)
    tool = _Tool()

    agent = KairosAgent.__new__(KairosAgent)      # the dispatch path reads only these
    agent.tools = [tool]
    agent.taint = None
    agent.agent_id = "a1"
    agent._current_project_id = "p1"
    agent.sentinel = gate

    result = asyncio.run(
        KairosAgent._dispatch_tool_with_args(agent, _ToolCall("terminal"), None))
    assert gate.calls == 1, "the dispatch path must prefer authorize_async"
    assert tool.ran is True
    assert result.success is True
