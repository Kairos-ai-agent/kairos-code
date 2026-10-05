"""Voice mode reaches the agent, not just the settings drawer.

Two levels, because both can break independently:

* the agent appends the "answer for the ear" directive when asked, and — just
  as important — leaves the normal chat path untouched when it is not;
* the HTTP route carries the flag from the request body into ``chat()``.

A settings switch that nothing reads is the failure this file exists to catch.
"""
from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos.agents.base import KairosAgent
from kairos.context_governor import DEFAULT_KEEP_RECENT_TOOL_RESULTS
from kairos.voice_text import VOICE_REPLY_DIRECTIVE


# ---------------------------------------------------------------------------
# The agent side
# ---------------------------------------------------------------------------


class _StubLLM:
    """Records the messages it is asked to complete. Never calls out."""

    def __init__(self):
        self.calls = []

    async def complete(self, messages, tools=None, temperature=None):
        self.calls.append(messages)
        return SimpleNamespace(content="短答。", tool_calls=[], usage={},
                               finish_reason="stop")


class _Harness(KairosAgent):
    """A KairosAgent with the heavy parts stubbed, so the chat path is walkable.

    Only the pieces ``_chat_impl`` actually touches are replaced. Everything
    under test — prompt assembly and the voice-mode append — is the real code.
    """

    #: Read-only on the real class (it derives from config); this harness has no
    #: config, so pin it.
    @property
    def _llm_timeout_s(self) -> float:
        return 5.0

    def _build_chat_system_prompt(self) -> str:
        return "BASE PROMPT"

    def _truncate_memory(self) -> None:
        pass

    def _sanitize_memory(self, memory):
        return list(memory)

    def _get_tool_schemas(self):
        return []

    @contextlib.contextmanager
    def _traced_llm_call(self, messages, schemas):
        yield SimpleNamespace(set_output=lambda **kwargs: None)


class _StubBus:
    """Records what the agent published, so the reply path is observable."""

    def __init__(self):
        self.published = []

    async def publish(self, message):
        self.published.append(message)


def _agent():
    agent = _Harness.__new__(_Harness)  # skip __init__: no orchestrator here
    agent.agent_id = "test-agent"
    agent.system_prompt = "BASE PROMPT"
    agent._llm = _StubLLM()
    agent._llm_config = None
    agent._memory = []
    agent._lock = asyncio.Lock()
    agent.temperature = 0.2
    agent.message_bus = _StubBus()
    agent.current_turn = 0
    agent.total_turns = 0
    agent.current_tool = None
    # _chat_impl -> _elided_memory reads this; the real __init__ sets it, and
    # this harness deliberately skips __init__.
    agent._keep_recent_tool_results = DEFAULT_KEEP_RECENT_TOOL_RESULTS
    return agent


def test_voice_mode_puts_the_directive_in_the_system_prompt():
    agent = _agent()
    asyncio.run(agent.chat("你好", voice_mode=True))
    system = agent._llm.calls[0][0]
    assert system.role == "system"
    assert system.content.startswith("BASE PROMPT")
    assert VOICE_REPLY_DIRECTIVE.strip() in system.content
    assert "Voice mode is on" in system.content


def test_ordinary_chat_is_untouched():
    """The default path must not grow a directive nobody asked for."""
    agent = _agent()
    asyncio.run(agent.chat("你好"))
    assert agent._llm.calls[0][0].content == "BASE PROMPT"


def test_the_directive_asks_for_short_speakable_prose():
    lowered = VOICE_REPLY_DIRECTIVE.lower()
    # Short, no Markdown, and the hand-off marker the filter cuts on.
    assert "details:" in lowered
    assert "markdown" in lowered
    assert "sentence" in lowered


# ---------------------------------------------------------------------------
# The route side
# ---------------------------------------------------------------------------


class _FakeCoder:
    def __init__(self):
        self.calls = []

    async def chat(self, text, *, voice_mode=False):
        self.calls.append({"text": text, "voice_mode": voice_mode})
        return "ok"


class _FakeProject:
    def __init__(self):
        self.id = "p1"
        self.coder = _FakeCoder()
        self.runtime = SimpleNamespace(attach_errors=[])
        self.work_dir = ""


class _FakeDb:
    def save_message(self, *args, **kwargs):
        return None


class _FakeOrchestrator:
    def __init__(self, project):
        self._project = project
        self._db = _FakeDb()

    def get_project(self, project_id):
        return self._project


@pytest.fixture()
def chat_client(monkeypatch):
    from api.routes import projects as projects_routes

    project = _FakeProject()
    monkeypatch.setattr(projects_routes, "_orch",
                        lambda: _FakeOrchestrator(project))
    app = FastAPI()
    app.include_router(projects_routes.router, prefix="/api/projects")
    return TestClient(app), project


def test_chat_route_forwards_voice_mode(chat_client):
    client, project = chat_client
    resp = client.post("/api/projects/p1/chat",
                       json={"message": "你好", "voice_mode": True})
    assert resp.status_code == 200
    assert project.coder.calls[-1]["voice_mode"] is True


def test_chat_route_defaults_to_off(chat_client):
    """An older client that omits the field gets the old behaviour."""
    client, project = chat_client
    resp = client.post("/api/projects/p1/chat", json={"message": "你好"})
    assert resp.status_code == 200
    assert project.coder.calls[-1]["voice_mode"] is False
