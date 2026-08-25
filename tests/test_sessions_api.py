"""Tests for the new /sessions API used by the chat-style sidebar."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

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


def _build_app(fake_db, projects_for_get=None):
    from kairos.core.orchestrator import Orchestrator
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

    from unittest.mock import patch
    with patch("kairos.core.orchestrator.Persistence", return_value=fake_db):
        orch = Orchestrator(model_router=router)

    # Inject a project record so get_project works.
    if projects_for_get:
        for p in projects_for_get:
            orch._projects[p.id] = p
    return orch


def _make_project(pid: str, name: str = "demo"):
    from kairos.core.orchestrator import Project
    from pathlib import Path
    p = Project(pid, name, "", Path(f"./workspace/{pid}"), "")
    p.coder = MagicMock()
    p.reviewer = MagicMock()
    return p


@pytest.fixture
def app_and_db(monkeypatch):
    from api.app import app
    from api import deps
    fake = _make_db()
    orch = _build_app(fake, [_make_project("p1"), _make_project("p2")])
    monkeypatch.setattr(deps, "orchestrator", orch)
    return TestClient(app), fake


# ---------------------------------------------------------------------------
# Persistence layer
# ---------------------------------------------------------------------------


def test_list_loop_sessions_empty(tmp_path):
    """No rounds saved → empty list."""
    from kairos.core.persistence import Persistence
    db = Persistence(tmp_path / "kairos.db")
    out = db.list_loop_sessions("p1")
    assert out == []


def test_list_loop_sessions_groups_by_session(tmp_path):
    from kairos.core.persistence import Persistence
    db = Persistence(tmp_path / "kairos.db")
    # Two sessions, three rounds total.
    db.save_loop_round("p1", "sess-A", 1, "first coder summary", {
        "summary": "lgtm", "score": 90, "approve": True,
    })
    time.sleep(0.01)
    db.save_loop_round("p1", "sess-A", 2, "second summary", {
        "summary": "still good", "score": 95, "approve": True,
    })
    time.sleep(0.01)
    db.save_loop_round("p1", "sess-B", 1, "another session", {
        "summary": "fix bug", "score": 60, "approve": False,
    })
    out = db.list_loop_sessions("p1")
    assert len(out) == 2
    # Newest first → sess-B before sess-A.
    assert out[0]["session_id"] == "sess-B"
    assert out[1]["session_id"] == "sess-A"
    # Counts / last-round / last-score.
    assert out[0]["round_count"] == 1
    assert out[0]["last_score"] == 60
    assert out[0]["last_approve"] is False
    assert out[1]["round_count"] == 2
    assert out[1]["last_round"] == 2
    assert out[1]["last_score"] == 95
    assert out[1]["last_approve"] is True


def test_list_loop_sessions_scoped_per_project(tmp_path):
    """Sessions for one project must NOT leak into another."""
    from kairos.core.persistence import Persistence
    db = Persistence(tmp_path / "kairos.db")
    db.save_loop_round("p1", "s1", 1, "x", {"summary": "s", "score": 80,
                                                "approve": True})
    db.save_loop_round("p2", "s2", 1, "y", {"summary": "s", "score": 70,
                                                "approve": True})
    assert [s["session_id"] for s in db.list_loop_sessions("p1")] == ["s1"]
    assert [s["session_id"] for s in db.list_loop_sessions("p2")] == ["s2"]


def test_load_session_rounds_returns_all_sorted(tmp_path):
    from kairos.core.persistence import Persistence
    db = Persistence(tmp_path / "kairos.db")
    db.save_loop_round("p1", "s1", 1, "first", {"summary": "ok", "score": 60,
                                                   "approve": False})
    time.sleep(0.01)
    db.save_loop_round("p1", "s1", 2, "second", {"summary": "ok", "score": 80,
                                                    "approve": True})
    rounds = db.load_session_rounds("p1", "s1")
    assert [r["round"] for r in rounds] == [1, 2]


def test_load_session_rounds_unknown_returns_empty(tmp_path):
    from kairos.core.persistence import Persistence
    db = Persistence(tmp_path / "kairos.db")
    assert db.load_session_rounds("p1", "missing") == []


# ---------------------------------------------------------------------------
# /api/projects/{id}/sessions endpoint
# ---------------------------------------------------------------------------


def test_sessions_endpoint_404_for_unknown_project(app_and_db):
    client, _ = app_and_db
    r = client.get("/api/projects/missing/sessions")
    assert r.status_code == 404


def test_sessions_endpoint_returns_persisted_sessions(app_and_db):
    client, fake = app_and_db
    fake.list_loop_sessions.return_value = [
        {"session_id": "sess-A", "round_count": 2, "last_round": 2,
         "last_score": 95, "last_approve": True,
         "started_at": 100.0, "last_activity": 110.0},
        {"session_id": "sess-B", "round_count": 1, "last_round": 1,
         "last_score": 60, "last_approve": False,
         "started_at": 200.0, "last_activity": 210.0},
    ]
    r = client.get("/api/projects/p1/sessions")
    assert r.status_code == 200
    data = r.json()
    assert data["project_id"] == "p1"
    assert [s["session_id"] for s in data["sessions"]] == ["sess-A", "sess-B"]
    fake.list_loop_sessions.assert_called_once_with("p1")


def test_sessions_endpoint_includes_live_session_at_top(app_and_db):
    """When the in-memory loop_session is alive, it appears at the top
    even if its rounds aren't persisted yet."""
    client, fake = app_and_db
    # Persisted sessions list (does NOT include the live one).
    fake.list_loop_sessions.return_value = [
        {"session_id": "old-sess", "round_count": 3, "last_round": 3,
         "last_score": 80, "last_approve": True,
         "started_at": 50.0, "last_activity": 60.0},
    ]
    # Build a project with a live session.
    from api import deps
    orch = deps.orchestrator
    p = orch.get_project("p1")
    live = MagicMock()
    live.session_id = "live-sess"
    live.round = 2
    live.last_score = 88
    live.last_approve = True
    live.started_at = 100.0
    p.loop_session = live
    p.loop_task = None  # not running
    r = client.get("/api/projects/p1/sessions")
    assert r.status_code == 200
    ids = [s["session_id"] for s in r.json()["sessions"]]
    assert ids[0] == "live-sess"  # promoted to top
    assert "old-sess" in ids


def test_sessions_endpoint_running_flag_set_correctly(app_and_db):
    client, fake = app_and_db
    fake.list_loop_sessions.return_value = [
        {"session_id": "s1", "round_count": 1, "last_round": 1,
         "last_score": 80, "last_approve": True,
         "started_at": 100.0, "last_activity": 110.0},
    ]
    from api import deps
    p = deps.orchestrator.get_project("p1")
    live = MagicMock()
    live.session_id = "s1"
    live.round = 1
    live.last_score = 80
    live.last_approve = True
    live.started_at = 100.0
    p.loop_session = live
    p.loop_task = MagicMock()
    p.loop_task.done.return_value = False  # still running
    data = client.get("/api/projects/p1/sessions").json()
    s = data["sessions"][0]
    assert s["running"] is True


# ---------------------------------------------------------------------------
# /api/projects/{id}/sessions/{sid}/rounds endpoint
# ---------------------------------------------------------------------------


def test_session_rounds_endpoint_404_for_unknown_project(app_and_db):
    client, _ = app_and_db
    r = client.get("/api/projects/missing/sessions/s1/rounds")
    assert r.status_code == 404


def test_session_rounds_endpoint_returns_persisted(app_and_db):
    client, fake = app_and_db
    fake.load_session_rounds.return_value = [
        {"project_id": "p1", "session_id": "s1", "round": 1,
         "coder_summary": "first", "review_summary": "lgtm",
         "review_json": None, "score": 80, "approve": 1,
         "created_at": 100.0, "insert_order": 1},
    ]
    r = client.get("/api/projects/p1/sessions/s1/rounds")
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "s1"
    assert len(data["rounds"]) == 1
    assert data["rounds"][0]["coder_summary"] == "first"
    fake.load_session_rounds.assert_called_once_with("p1", "s1")


def test_session_rounds_merges_in_memory_history(app_and_db):
    """If the live session matches, append history rows whose round
    isn't already on disk."""
    client, fake = app_and_db
    fake.load_session_rounds.return_value = [
        {"project_id": "p1", "session_id": "s1", "round": 1,
         "coder_summary": "first", "review_summary": "ok",
         "review_json": None, "score": 80, "approve": 1,
         "created_at": 100.0, "insert_order": 1},
    ]
    from api import deps
    p = deps.orchestrator.get_project("p1")
    live = MagicMock()
    live.session_id = "s1"
    live.history = [
        {"round": 1, "coder_summary": "first", "review": {"summary": "ok",
                                                            "score": 80,
                                                            "approve": True},
         "created_at": 100.0},
        {"round": 2, "coder_summary": "second", "review": {"summary": "ok2",
                                                              "score": 90,
                                                              "approve": True},
         "created_at": 110.0},
    ]
    p.loop_session = live
    r = client.get("/api/projects/p1/sessions/s1/rounds")
    assert r.status_code == 200
    rounds = r.json()["rounds"]
    assert len(rounds) == 2  # R1 from disk + R2 from memory
    assert rounds[1]["round"] == 2
    assert rounds[1]["coder_summary"] == "second"
    assert rounds[1]["score"] == 90
