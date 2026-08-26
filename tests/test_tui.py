"""Tests for the Kairos TUI controller and backend client.

We exercise the :class:`TuiController` end-to-end against a fake
backend (no real Textual event loop, no real HTTP) so the tests
are fast and deterministic.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from kairos.tui import BackendClient, TuiController, TuiState, Turn


# ---------------------------------------------------------------------------
# Fake backend
# ---------------------------------------------------------------------------


class FakeBackend:
    """In-memory replacement for the FastAPI backend.

    The TUI tests don't need real network — we hand the controller
    a fake that returns canned responses for the few endpoints the
    TUI calls.
    """

    def __init__(self) -> None:
        self.coder_mode = "default"
        self.sessions = [{"id": "s1", "round": 2}]
        self.ask_id = 0
        self.messages: List[Dict[str, Any]] = []
        self.answers: List[Dict[str, Any]] = []
        # Pre-baked response
        self.next_post_response: Dict[str, Any] = {
            "status": "ok", "ask_id": "", "response": "(stub)",
        }

    # Mirrors the BackendClient interface.
    async def list_projects(self):
        return [{"id": "p1", "name": "demo"}]

    async def list_sessions(self, project_id: str):
        return list(self.sessions)

    async def get_session_rounds(self, project_id: str, session_id: str):
        return []

    async def post_message(self, project_id: str, text: str):
        self.messages.append({"project_id": project_id, "text": text})
        return dict(self.next_post_response)

    async def post_answer(self, project_id: str, ask_id: str, text: str):
        self.answers.append({"project_id": project_id, "ask_id": ask_id, "text": text})
        return {"status": "ok", "response": "thanks for clarifying"}

    async def start_loop(self, project_id: str, requirement: str):
        return {"status": "started", "session_id": "s1"}

    async def stop_loop(self, project_id: str):
        return {"status": "stopped"}

    async def set_coder_mode(self, project_id: str, mode: str):
        self.coder_mode = mode
        return {"mode": mode}

    async def get_coder_mode(self, project_id: str):
        return {"mode": self.coder_mode}


@pytest.fixture
def fake_backend():
    return FakeBackend()


# ---------------------------------------------------------------------------
# Turn + state
# ---------------------------------------------------------------------------


def test_turn_render_user():
    t = Turn(role="user", content="hi")
    out = t.render()
    assert "you" in out
    assert "hi" in out


def test_turn_render_coder():
    t = Turn(role="coder", content="hello")
    out = t.render()
    assert "coder" in out


def test_turn_render_with_tool():
    t = Turn(role="tool", content="ls", tool="terminal")
    out = t.render()
    assert "tool" in out
    assert "(terminal)" in out


def test_state_add_and_transcript():
    s = TuiState(project_id="p1", project_name="demo")
    s.add_turn("user", "hi")
    s.add_turn("coder", "hello")
    transcript = s.transcript()
    assert "you" in transcript
    assert "hi" in transcript
    assert "coder" in transcript
    assert "hello" in transcript


def test_state_header_shape():
    s = TuiState(project_id="p1", project_name="demo",
                 coder_mode="sandbox", round=3)
    out = s.header()
    assert "demo" in out
    assert "sandbox" in out
    assert "round=3" in out
    assert "running=no" in out


def test_state_running_flag_flips_header():
    s = TuiState()
    s.running = True
    assert "running=yes" in s.header()


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_pulls_session_and_mode(fake_backend):
    fake_backend.coder_mode = "read_only"
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    # patch refresh to use our fake
    controller.backend = fake_backend  # type: ignore[assignment]
    await controller.refresh()
    assert controller.state.coder_mode == "read_only"
    assert controller.state.session_id == "s1"
    assert controller.state.round == 2


@pytest.mark.asyncio
async def test_submit_sends_to_backend(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    fake_backend.next_post_response = {
        "status": "ok", "ask_id": "", "response": "hi back",
    }
    await controller.submit("hello agent")
    assert fake_backend.messages == [{"project_id": "p1", "text": "hello agent"}]
    # User + coder turns recorded
    assert any(t.role == "user" and t.content == "hello agent" for t in controller.state.turns)
    assert any(t.role == "coder" and t.content == "hi back" for t in controller.state.turns)


@pytest.mark.asyncio
async def test_submit_records_pending_ask(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    fake_backend.next_post_response = {
        "status": "pending", "ask_id": "ask-42", "response": "which file?",
    }
    await controller.submit("do the thing")
    assert controller.state.pending_ask == {
        "status": "pending", "ask_id": "ask-42", "response": "which file?",
    }
    # system turn announcing clarification
    assert any(
        t.role == "system" and "clarification" in t.content
        for t in controller.state.turns
    )


@pytest.mark.asyncio
async def test_answer_pending_uses_ask_id(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    fake_backend.next_post_response = {
        "status": "pending", "ask_id": "ask-9", "response": "which?",
    }
    await controller.submit("hi")
    assert controller.state.pending_ask
    await controller.answer_pending("the file is x.py")
    assert fake_backend.answers == [{
        "project_id": "p1", "ask_id": "ask-9", "text": "the file is x.py",
    }]
    assert controller.state.pending_ask is None
    # assistant response recorded
    assert any(
        t.role == "coder" and t.content == "thanks for clarifying"
        for t in controller.state.turns
    )


@pytest.mark.asyncio
async def test_answer_pending_when_no_ask_logs(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    await controller.answer_pending("blah")
    assert any("no pending ask" in t.content for t in controller.state.turns)


@pytest.mark.asyncio
async def test_submit_empty_string_is_noop(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    await controller.submit("")
    await controller.submit("   ")
    assert fake_backend.messages == []


@pytest.mark.asyncio
async def test_slash_command_runs_locally(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    # /help is a built-in slash command — must not hit the backend
    await controller.submit("/help")
    assert fake_backend.messages == []
    assert any(t.role == "system" and "/test" in t.content for t in controller.state.turns)


@pytest.mark.asyncio
async def test_slash_command_error_logged(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    await controller.submit("/no_such_command")
    assert any("error" in t.content for t in controller.state.turns)


@pytest.mark.asyncio
async def test_submit_records_running_flag(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]
    fake_backend.next_post_response = {
        "status": "ok", "ask_id": "", "response": "ok",
    }
    # Capture running flag during the call.
    observed = []

    original = fake_backend.post_message
    async def spy(project_id, text):
        observed.append(controller.state.running)
        return await original(project_id, text)
    fake_backend.post_message = spy  # type: ignore[assignment]

    await controller.submit("hi")
    # While the call was in flight, running was True
    assert observed == [True]
    # After completion, running is back to False
    assert controller.state.running is False


@pytest.mark.asyncio
async def test_network_error_recorded(fake_backend):
    controller = TuiController(backend=None, project_id="p1")  # type: ignore[arg-type]
    controller.backend = fake_backend  # type: ignore[assignment]

    async def boom(*a, **kw):
        raise RuntimeError("connection refused")
    fake_backend.post_message = boom  # type: ignore[assignment]

    await controller.submit("hi")
    assert any("network error" in t.content for t in controller.state.turns)
    assert controller.state.running is False
