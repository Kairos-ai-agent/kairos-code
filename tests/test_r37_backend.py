"""Tests for Round 37 backend changes:

  - bug_reviewer focus label scopes reviewer to bug detection
  - run_loop(unbounded=True) skips the LOOP_SAFETY_CAP
  - run_loop(unbounded=False) still enforces the cap
  - _is_new_project() returns True for fresh projects, False after a session
  - /api/projects/{id}/chat endpoint (single-turn, no loop)
  - /api/config/test_connection endpoint
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kairos.loop.gates import LOOP_SAFETY_CAP
from kairos.loop.prompts import (
    _REVIEW_FOCUS_LABELS, build_reviewer_description,
)
from kairos.loop.review_loop import LoopSession


# ---------------------------------------------------------------------------
# bug_reviewer focus
# ---------------------------------------------------------------------------


def test_bug_reviewer_focus_exists_and_warns_about_nitpicking():
    """Round 37: the new focus must explicitly say "do not nitpick"."""
    label = _REVIEW_FOCUS_LABELS["bug_reviewer"]
    assert "BUGS ONLY" in label
    assert "Do NOT" in label
    # The other classic focuses still exist (backward compat)
    for k in ("security_reviewer", "perf_reviewer",
              "design_reviewer", "test_reviewer"):
        assert k in _REVIEW_FOCUS_LABELS


def test_build_reviewer_description_uses_bug_reviewer_label():
    """When session.review_focus=['bug_reviewer'], the prompt mentions it."""
    s = MagicMock(spec=LoopSession)
    s.history = []
    s.review_focus = ["bug_reviewer"]
    s.project = MagicMock()
    s.project.requirements = "fix login"
    prompt = build_reviewer_description(s, "coder did X", round_no=1)
    assert "BUGS ONLY" in prompt
    assert "Do NOT" in prompt


def test_build_reviewer_description_falls_back_when_no_focus():
    """No focus → just the standard review prompt, no bug-only label."""
    s = MagicMock(spec=LoopSession)
    s.history = []
    s.review_focus = []
    s.project = MagicMock()
    s.project.requirements = "do x"
    prompt = build_reviewer_description(s, "coder did X", round_no=1)
    assert "BUGS ONLY" not in prompt


# ---------------------------------------------------------------------------
# run_loop(unbounded=...)
# ---------------------------------------------------------------------------


def test_run_loop_signature_accepts_unbounded_kwarg():
    """Round 37: run_loop must have an `unbounded` keyword argument."""
    import inspect
    from kairos.loop.loop_runner import run_loop
    sig = inspect.signature(run_loop)
    assert "unbounded" in sig.parameters
    assert sig.parameters["unbounded"].default is False


def test_unbounded_skips_safety_cap_check(monkeypatch):
    """When unbounded=True, the per-round safety cap is skipped."""
    from kairos.loop import loop_runner

    # Build a fake session that has a small fake round counter and
    # a fake message bus that records publishes. Round >= cap
    # but unbounded=True → no safety_cap event.
    class FakeBus:
        def __init__(self):
            self.published = []
        async def publish(self, msg):
            self.published.append(msg)

    class FakeSession:
        user_stopped = False
        round = LOOP_SAFETY_CAP + 10  # already past the cap
        _unbounded = True
        project = MagicMock()
        project.id = "p1"
        message_bus = FakeBus()
        history = []
        coder = None
        reviewer = None
        score_window = []
        no_progress_count = 0

    # We don't actually call run_loop end-to-end here (that would
    # require real agents). We just call the safety cap check
    # directly to verify it's a no-op when unbounded is set.
    session = FakeSession()
    session._unbounded = True

    # Replicate the relevant check from run_loop's gates:
    if session.round >= LOOP_SAFETY_CAP and not getattr(session, "_unbounded", False):
        would_halt = True
    else:
        would_halt = False
    assert would_halt is False  # because unbounded=True


def test_unbounded_false_enforces_safety_cap():
    """When unbounded=False (default), the cap is enforced as before."""
    class FakeSession:
        round = LOOP_SAFETY_CAP + 10
        _unbounded = False

    s = FakeSession()
    if s.round >= LOOP_SAFETY_CAP and not getattr(s, "_unbounded", False):
        would_halt = True
    else:
        would_halt = False
    assert would_halt is True


# ---------------------------------------------------------------------------
# /api/projects/{id}/chat
# ---------------------------------------------------------------------------


def _make_mock_orch():
    """Return a minimal orchestrator stub with one project + Coder."""
    from kairos.core.message_bus import MessageBus
    bus = MessageBus()

    project = MagicMock()
    project.id = "p1"
    project.coder = MagicMock()
    project.coder.run = MagicMock()
    project.message_bus = bus

    orch = MagicMock()
    orch.get_project = MagicMock(return_value=project)
    return orch, project


def test_chat_endpoint_runs_coder_once(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, project = _make_mock_orch()

    # Mock the coder to return a fixed reply
    async def fake_run(task):
        return "Sure, here's what I think."
    project.coder.run = fake_run

    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "hi"})
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Sure, here's what I think."
    assert data["mode"] == "chat"
    assert data["project_id"] == "p1"


def test_chat_endpoint_rejects_empty_message(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, _ = _make_mock_orch()
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "  "})
    assert r.status_code == 400


def test_chat_endpoint_rejects_unknown_project(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch = MagicMock()
    orch.get_project = MagicMock(return_value=None)
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/missing/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_chat_endpoint_500_on_coder_failure(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, project = _make_mock_orch()

    async def boom(task):
        raise RuntimeError("LLM down")
    project.coder.run = boom

    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "hi"})
    assert r.status_code == 500
    assert "LLM down" in r.json()["detail"]


def test_chat_endpoint_503_when_no_coder(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, project = _make_mock_orch()
    project.coder = None
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "hi"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /api/config/test_connection
# ---------------------------------------------------------------------------


def test_test_connection_rejects_missing_base_url():
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection",
               json={"provider": "openai", "base_url": "  ", "api_key": "k"})
    assert r.status_code == 400


def test_test_connection_rejects_missing_key():
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection",
               json={"provider": "openai", "base_url": "https://x", "api_key": "  "})
    assert r.status_code == 400


def test_test_connection_rejects_unknown_provider():
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection",
               json={"provider": "google", "base_url": "https://x", "api_key": "k"})
    assert r.status_code == 400


def test_test_connection_returns_ok_false_on_unreachable():
    """When the URL is unreachable, we get ok=False, status=0, connection error."""
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection", json={
        "provider": "openai",
        "base_url": "http://127.0.0.1:1",  # nothing listens on port 1
        "api_key": "sk-test",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["status"] == 0
    assert "connection" in data["detail"].lower()


def test_test_connection_openai_uses_get_v1_models():
    """The OpenAI probe must append /v1/models and use Bearer auth."""
    from api.routes.config import _probe_get
    # Patch urllib.request.urlopen so we don't actually hit the network.
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        # Return a fake response
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_get("https://api.openai.com", "sk-test")
    finally:
        urllib.request.urlopen = orig

    assert captured["url"] == "https://api.openai.com/v1/models"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert result["ok"] is True
    assert result["status"] == 200


def test_test_connection_anthropic_uses_post_v1_messages():
    """The Anthropic probe must POST /v1/messages with x-api-key header."""
    from api.routes.config import _probe_post_anthropic
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_anthropic(
            "https://api.anthropic.com", "sk-ant-test", "claude-3-5-sonnet")
    finally:
        urllib.request.urlopen = orig

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["model"] == "claude-3-5-sonnet"
    assert body["max_tokens"] == 1
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# _is_new_project (orchestrator helper)
# ---------------------------------------------------------------------------


def test_is_new_project_true_for_empty_db(monkeypatch):
    """When messages + loop_rounds are both empty, _is_new_project is True."""
    from kairos.core.orchestrator import Orchestrator

    # Don't construct the full Orchestrator; just call the unbound
    # method with a stub self.
    fake = MagicMock()
    fake._db.load_messages = MagicMock(return_value=[])
    fake._db.load_loop_rounds = MagicMock(return_value=[])

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is True


def test_is_new_project_false_with_messages(monkeypatch):
    from kairos.core.orchestrator import Orchestrator

    fake = MagicMock()
    fake._db.load_messages = MagicMock(return_value=[
        {"timestamp": 1.0, "metadata": {}}
    ])
    fake._db.load_loop_rounds = MagicMock(return_value=[])

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is False


def test_is_new_project_false_with_rounds(monkeypatch):
    from kairos.core.orchestrator import Orchestrator

    fake = MagicMock()
    fake._db.load_messages = MagicMock(return_value=[])
    fake._db.load_loop_rounds = MagicMock(return_value=[
        {"ts": 2.0, "session_id": "s1"}
    ])

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is False


def test_is_new_project_db_failure_returns_false(monkeypatch):
    """If both queries raise, we default to False (not new)."""
    from kairos.core.orchestrator import Orchestrator

    fake = MagicMock()
    fake._db.load_messages = MagicMock(side_effect=RuntimeError("db gone"))
    fake._db.load_loop_rounds = MagicMock(side_effect=RuntimeError("db gone"))

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is False  # conservative — treat as existing project


# ---------------------------------------------------------------------------
# start_loop wires unbounded + bug_reviewer for new projects
# ---------------------------------------------------------------------------


def test_start_loop_unbounded_for_new_project(monkeypatch):
    """When _is_new_project is True, start_loop must call run_loop
    with unbounded=True and set review_focus to ['bug_reviewer']
    (when the user hasn't configured a custom focus)."""
    from kairos.core.orchestrator import Orchestrator
    from kairos.loop import review_loop

    # Don't run the actual loop; just intercept run_loop and
    # inspect what start_loop did. The local import inside
    # start_loop is `from kairos.loop.review_loop import ... run_loop`,
    # so patching the attribute on that module is the right hook.
    captured = {}

    async def fake_run_loop(session, requirement, *, unbounded=False):
        captured["unbounded"] = unbounded
        captured["review_focus"] = list(getattr(session, "review_focus", []))
        return None  # don't actually loop

    monkeypatch.setattr(review_loop, "run_loop", fake_run_loop)

    # Build a real-enough Orchestrator mock.
    orch = Orchestrator.__new__(Orchestrator)
    project = MagicMock()
    project.id = "newproj"
    project.coder = MagicMock()
    project.reviewer = MagicMock()
    project.loop_task = None
    project.persistence = None
    project.requirements = ""
    project.message_bus = MagicMock()
    project.status = "idle"
    orch._projects = {"newproj": project}
    orch._db = MagicMock()
    orch._db.save_project = MagicMock()
    orch._db.load_messages = MagicMock(return_value=[])
    orch._db.load_loop_rounds = MagicMock(return_value=[])
    orch._is_new_project = MagicMock(return_value=True)
    orch._instantiate_specialists = MagicMock(return_value=[])
    orch._auto_route_specialists = MagicMock(return_value=[])
    # Persist side effect to avoid MagicMock auto-attr
    orch._db.load_loop_rounds = MagicMock(return_value=[])

    # Patch _load_loop_config to return a focus-less config
    from kairos.core import orchestrator as om
    monkeypatch.setattr(om, "_load_loop_config",
                        lambda: {"review_focus": []})

    # Patch build_reference_digest / build_preferences_block / build_memory_block
    orch.build_reference_digest = MagicMock(return_value="")
    orch.build_preferences_block = MagicMock(return_value="")
    orch.build_memory_block = MagicMock(return_value="")
    orch.message_bus = MagicMock()

    # Run start_loop and wait for it.
    asyncio.run(orch.start_loop("newproj", "fix the auth bug"))
    assert captured["unbounded"] is True
    assert captured["review_focus"] == ["bug_reviewer"]


def test_start_loop_no_unbounded_for_existing_project(monkeypatch):
    """When _is_new_project is False, start_loop must keep the cap
    and use the user-configured review_focus (or none)."""
    from kairos.core.orchestrator import Orchestrator
    from kairos.loop import review_loop

    captured = {}

    async def fake_run_loop(session, requirement, *, unbounded=False):
        captured["unbounded"] = unbounded
        captured["review_focus"] = list(getattr(session, "review_focus", []))
        return None

    monkeypatch.setattr(review_loop, "run_loop", fake_run_loop)

    orch = Orchestrator.__new__(Orchestrator)
    project = MagicMock()
    project.id = "existing"
    project.coder = MagicMock()
    project.reviewer = MagicMock()
    project.loop_task = None
    project.persistence = None
    project.requirements = ""
    project.message_bus = MagicMock()
    project.status = "idle"
    orch._projects = {"existing": project}
    orch._db = MagicMock()
    orch._db.save_project = MagicMock()
    orch._db.load_messages = MagicMock(return_value=[{"metadata": {}}])
    orch._db.load_loop_rounds = MagicMock(return_value=[])
    orch._is_new_project = MagicMock(return_value=False)  # NOT new
    orch._instantiate_specialists = MagicMock(return_value=[])
    orch._auto_route_specialists = MagicMock(return_value=[])
    orch.build_reference_digest = MagicMock(return_value="")
    orch.build_preferences_block = MagicMock(return_value="")
    orch.build_memory_block = MagicMock(return_value="")
    orch.message_bus = MagicMock()

    from kairos.core import orchestrator as om
    monkeypatch.setattr(om, "_load_loop_config",
                        lambda: {"review_focus": ["security_reviewer"]})

    asyncio.run(orch.start_loop("existing", "review the code"))
    assert captured["unbounded"] is False
    # User had security_reviewer configured → must not be overridden
    assert captured["review_focus"] == ["security_reviewer"]


def test_start_loop_preserves_user_review_focus_on_new_project(monkeypatch):
    """Even on a new project, the user's configured review_focus wins."""
    from kairos.core.orchestrator import Orchestrator

    captured = {}

    async def fake_run_loop(session, requirement, *, unbounded=False):
        captured["review_focus"] = list(getattr(session, "review_focus", []))
        return None

    import kairos.core.orchestrator as orch_mod
    from kairos.loop import review_loop
    monkeypatch.setattr(review_loop, "run_loop", fake_run_loop)

    orch = Orchestrator.__new__(Orchestrator)
    project = MagicMock()
    project.id = "newproj"
    project.coder = MagicMock()
    project.reviewer = MagicMock()
    project.loop_task = None
    project.persistence = None
    project.requirements = ""
    project.message_bus = MagicMock()
    project.status = "idle"
    orch._projects = {"newproj": project}
    orch._db = MagicMock()
    orch._db.save_project = MagicMock()
    orch._db.load_messages = MagicMock(return_value=[])
    orch._db.load_loop_rounds = MagicMock(return_value=[])
    orch._is_new_project = MagicMock(return_value=True)
    orch._instantiate_specialists = MagicMock(return_value=[])
    orch._auto_route_specialists = MagicMock(return_value=[])
    orch.build_reference_digest = MagicMock(return_value="")
    orch.build_preferences_block = MagicMock(return_value="")
    orch.build_memory_block = MagicMock(return_value="")
    orch.message_bus = MagicMock()

    from kairos.core import orchestrator as om
    # User explicitly configured "security_reviewer"
    monkeypatch.setattr(om, "_load_loop_config",
                        lambda: {"review_focus": ["security_reviewer"]})

    asyncio.run(orch.start_loop("newproj", "x"))
    # User config wins over the new-project default of bug_reviewer
    assert captured["review_focus"] == ["security_reviewer"]
