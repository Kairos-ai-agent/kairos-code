"""Tests for output guardrails (Codex-Harness-style review hooks)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.agents.base import AgentTask, KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.guardrails import (
    GuardrailResult,
    GuardrailTripwire,
    OutputGuardrail,
    _pick_max_severity,
    make_deny_substrings_guardrail,
)
from kairos.llm.base import LLMConfig
from kairos.llm.provider_registry import ProviderRegistry


class StubProvider:
    def __init__(self, content: str = ""):
        self.config = LLMConfig(provider="guard_stub", model="x", api_key="sk")
        self._content = content
    async def complete(self, messages, tools=None, **kw):
        from kairos.llm.base import LLMResponse
        return LLMResponse(content=self._content, model="x", usage={},
                           finish_reason="stop")
    async def stream(self, messages, **kw):
        yield ""
    async def close(self):
        pass


ProviderRegistry.register("guard_stub", StubProvider)


# ---------------------------------------------------------------------------
# Pure-function tests (no LLM)
# ---------------------------------------------------------------------------

def test_pick_max_severity_empty():
    assert _pick_max_severity([]) == "NONE"


def test_pick_max_severity_orders_correctly():
    issues = [
        {"severity": "MINOR"},
        {"severity": "CRITICAL"},
        {"severity": "MAJOR"},
    ]
    assert _pick_max_severity(issues) == "CRITICAL"
    issues2 = [{"severity": "MAJOR"}, {"severity": "MINOR"}]
    assert _pick_max_severity(issues2) == "MAJOR"
    issues3 = [{"severity": "MINOR"}, {"severity": "SUGGESTION"}]
    assert _pick_max_severity(issues3) == "MINOR"


def test_deny_substrings_guardrail_passes_on_clean():
    g = make_deny_substrings_guardrail(["AKIA"])
    r = g("nothing secret here", context=None)
    assert r.tripwire is False
    assert r.severity == "NONE"


def test_deny_substrings_guardrail_trips_on_match():
    g = make_deny_substrings_guardrail(["AKIA", "BEGIN PRIVATE KEY"])
    r = g("leaked: AKIA12345ABCDE", context=None)
    assert r.tripwire is True
    assert r.severity == "CRITICAL"
    # Summary uses the original case (we lowercased only for matching).
    assert "AKIA" in r.summary or "akia" in r.summary


# ---------------------------------------------------------------------------
# OutputGuardrail (with stub reviewer)
# ---------------------------------------------------------------------------

class FakeReviewer:
    def __init__(self, verdict_text: str):
        self.role = "reviewer"
        self.name = "FakeReviewer"
        self._verdict_text = verdict_text
    async def run(self, task):
        return self._verdict_text


def _critical_verdict() -> str:
    return """{
        "approve": false,
        "score": 40,
        "issues": [
            {"category": "security", "severity": "CRITICAL",
             "file": "x.py", "line": 1, "description": "leaked secret",
             "fix_instruction": "remove the key"}
        ],
        "summary": "CRITICAL: leaked secret in x.py line 1"
    }"""


def _clean_verdict() -> str:
    return '{"approve": true, "score": 95, "issues": [], "summary": "lgtm"}'


def _minor_verdict() -> str:
    return """{
        "approve": true,
        "score": 82,
        "issues": [
            {"category": "quality", "severity": "MINOR",
             "file": "x.py", "line": 2, "description": "naming nit",
             "fix_instruction": "rename f to foo"}
        ],
        "summary": "one minor nit"
    }"""


async def test_guardrail_trips_on_critical_issue():
    bus = MessageBus()
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_critical_verdict()),
        blocking=False, message_bus=bus,
    )
    result = await guard.check("test.agent", "here is some output")
    assert result.tripwire is True
    assert result.severity == "CRITICAL"
    assert "leaked secret" in result.summary


async def test_guardrail_passes_on_clean_verdict():
    bus = MessageBus()
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_clean_verdict()),
        blocking=False, message_bus=bus,
    )
    result = await guard.check("test.agent", "some output")
    assert result.tripwire is False
    assert result.severity == "NONE"


async def test_guardrail_minor_does_not_trip():
    bus = MessageBus()
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_minor_verdict()),
        blocking=False, message_bus=bus,
    )
    result = await guard.check("test.agent", "some output")
    assert result.tripwire is False
    assert result.severity == "MINOR"


async def test_guardrail_blocking_raises_on_critical():
    bus = MessageBus()
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_critical_verdict()),
        blocking=True, message_bus=bus,
    )
    with pytest.raises(GuardrailTripwire):
        await guard.check("test.agent", "x")


async def test_guardrail_blocking_passes_on_clean():
    bus = MessageBus()
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_clean_verdict()),
        blocking=True, message_bus=bus,
    )
    # Should not raise.
    r = await guard.check("test.agent", "x")
    assert r.tripwire is False


async def test_guardrail_publishes_to_bus():
    bus = MessageBus()
    received = []
    bus.add_listener(received.append)
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_critical_verdict()),
        blocking=False, message_bus=bus,
    )
    await guard.check("test.agent", "output text")
    assert any("guardrail.test.agent" in str(m.topic) for m in received), \
        f"expected guardrail message, got {[m.topic for m in received]}"


async def test_guardrail_handles_empty_output():
    bus = MessageBus()
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_clean_verdict()),
        blocking=False, message_bus=bus,
    )
    r = await guard.check("test.agent", "")
    # Should skip reviewer call entirely.
    assert r.summary == "empty output; guardrail skipped"
    assert r.tripwire is False


async def test_guardrail_handles_reviewer_crash():
    class CrashingReviewer:
        role = "reviewer"; name = "x"
        async def run(self, task):
            raise RuntimeError("LLM down")
    bus = MessageBus()
    guard = OutputGuardrail(
        reviewer=CrashingReviewer(), blocking=False, message_bus=bus,
    )
    r = await guard.check("test.agent", "output")
    assert r.tripwire is False
    assert r.error is not None
    assert "reviewer call failed" in r.error


# ---------------------------------------------------------------------------
# Integration with KairosAgent: task.guardrail gets populated
# ---------------------------------------------------------------------------

class OnceProvider:
    """Returns no-tool-call response immediately (so the agent loop
    converges in a single turn and the guardrail hook runs)."""
    def __init__(self, config, content="done"):
        self.config = config
        self._content = content
    async def complete(self, messages, tools=None, **kw):
        from kairos.llm.base import LLMResponse
        return LLMResponse(content=self._content, model="x", usage={},
                           finish_reason="stop")
    async def stream(self, messages, **kw):
        # Yield the whole content as one chunk so the streaming path
        # matches what the non-streaming path would have produced.
        yield self._content
    async def close(self):
        pass


ProviderRegistry.register("once_p", OnceProvider)


async def test_kairos_agent_runs_guardrail_after_task():
    bus = MessageBus()
    agent = KairosAgent(
        agent_id="t.g", name="T", role="test",
        system_prompt="sys",
        llm_config=LLMConfig(provider="once_p", model="x", api_key="sk"),
        message_bus=bus,
    )
    guard = OutputGuardrail(
        reviewer=FakeReviewer(_critical_verdict()),
        blocking=False, message_bus=bus,
    )
    agent._output_guardrail = guard
    task = AgentTask(id="t1", title="t", description="d")
    result = await agent.run(task)
    # The agent's own output is preserved; the guardrail verdict is
    # attached separately so the UI / orchestrator can see it.
    assert result == "done"
    assert task.guardrail is not None
    assert task.guardrail["tripwire"] is True
    assert task.guardrail["severity"] == "CRITICAL"


async def test_kairos_agent_without_guardrail_unchanged():
    bus = MessageBus()
    agent = KairosAgent(
        agent_id="t.n", name="T", role="test",
        system_prompt="sys",
        llm_config=LLMConfig(provider="once_p", model="x", api_key="sk"),
        message_bus=bus,
    )
    assert agent._output_guardrail is None
    task = AgentTask(id="t1", title="t", description="d")
    result = await agent.run(task)
    assert result == "done"
    assert task.guardrail is None  # not set
