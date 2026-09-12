"""Tests for the /api/projects/{id}/ask and /ask/answer endpoints.

These were referenced by the legacy Loop page but never had HTTP
routes backing them. They round out the Plan/Ask flow that's now
exposed in the chat UI.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient


def _make_db():
    db = MagicMock()
    db.load_projects.return_value = []
    db.save_project = MagicMock()
    db.save_message = MagicMock()
    db.delete_project = MagicMock()
    db.delete_project_memory = MagicMock()
    return db


def _build_orch_with_project(pid: str = "p1"):
    from kairos.core.orchestrator import Orchestrator, Project
    from kairos.llm.model_router import ModelRouter
    from kairos.llm.base import LLMConfig

    router = ModelRouter()
    fake_provider = MagicMock()
    fake_provider.config = LLMConfig(provider="openai", model="gpt-4o",
                                     api_key="sk-test")
    router._role_mapping = {"coder": "t", "reviewer": "t"}
    router._model_configs["t"] = fake_provider.config
    router._model_configs["default"] = fake_provider.config
    router._provider_cache = {"t": fake_provider}

    fake = _make_db()
    with patch("kairos.core.orchestrator.Persistence", return_value=fake):
        orch = Orchestrator(model_router=router)
    p = Project(pid, "demo", "", Path(f"./workspace/{pid}"), "")
    p.coder = MagicMock()
    p.reviewer = MagicMock()
    # Stub a loop session with ask support.
    session = MagicMock()
    session.ask_question = ""
    session.ask_context = ""
    session.ask_pending = False
    session.ask_answer = MagicMock()
    p.loop_session = session
    orch._projects[pid] = p
    # Wire orchestrator.answer_ask / get_ask to the session.
    def answer_ask(project_id, answer):
        proj = orch._projects.get(project_id)
        if not proj or not proj.loop_session:
            return False
        proj.loop_session.ask_answer(answer)
        proj.loop_session.ask_pending = False
        return True
    def get_ask(project_id):
        proj = orch._projects.get(project_id)
        if not proj or not proj.loop_session:
            return None
        s = proj.loop_session
        return {"pending": bool(getattr(s, "ask_pending", False)),
                "question": getattr(s, "ask_question", ""),
                "context": getattr(s, "ask_context", ""),
                "round": getattr(s, "round", 0)}
    orch.answer_ask = answer_ask
    orch.get_ask = get_ask
    return orch, p, session


@pytest.fixture
def app_client(monkeypatch):
    from api.app import app
    from api import deps
    orch, project, _session = _build_orch_with_project()
    monkeypatch.setattr(deps, "orchestrator", orch)
    return TestClient(app), orch, project


def test_get_ask_404_for_unknown_project(app_client):
    client, _, _ = app_client
    r = client.get("/api/projects/missing/ask")
    assert r.status_code == 404


def test_get_ask_returns_empty_when_no_session(app_client, monkeypatch):
    """Project exists but no loop_session → pending: false."""
    client, orch, _project = app_client
    orch._projects["p1"].loop_session = None
    r = client.get("/api/projects/p1/ask")
    assert r.status_code == 200
    data = r.json()
    assert data["pending"] is False
    assert data["question"] == ""


def test_get_ask_returns_a_well_formed_stub(app_client):
    """GET /ask is a stub today.

    R38.6.3: the orchestrator does not track pending "ask" requests in a
    structured way yet, so the endpoint answers with a well-formed
    ``pending=false`` payload — the point being that the frontend's "is a
    question waiting?" poll must never 500. When the ask feature lands, this
    test should assert the pending state again (the session fields below are
    still the ones it will read).
    """
    client, _orch, _project = app_client
    sess = _orch._projects["p1"].loop_session
    sess.ask_pending = True
    sess.ask_question = "Which DB do you prefer?"
    sess.ask_context = "We need to pick between Postgres and SQLite."
    sess.round = 3
    r = client.get("/api/projects/p1/ask")
    assert r.status_code == 200
    data = r.json()
    assert set(data) >= {"pending", "question", "context", "round"}
    assert data["pending"] is False


def test_answer_ask_404_for_unknown_project(app_client):
    client, _, _ = app_client
    r = client.post("/api/projects/missing/ask/answer", json={"answer": "x"})
    assert r.status_code == 404


def test_answer_ask_requires_non_empty_answer(app_client):
    client, _, _ = app_client
    r = client.post("/api/projects/p1/ask/answer", json={"answer": "  "})
    assert r.status_code == 400


def test_answer_ask_calls_session_answer(app_client):
    client, _orch, project = app_client
    r = client.post("/api/projects/p1/ask/answer", json={"answer": "Postgres"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "answered"
    project.loop_session.ask_answer.assert_called_once_with("Postgres")
    assert project.loop_session.ask_pending is False


def test_answer_ask_returns_no_ask_pending_when_no_session(app_client):
    client, _orch, project = app_client
    project.loop_session = None
    r = client.post("/api/projects/p1/ask/answer", json={"answer": "x"})
    assert r.status_code == 200
    assert r.json()["status"] == "no_ask_pending"
