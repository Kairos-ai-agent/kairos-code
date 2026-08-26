"""API tests for the /coder_mode endpoints on /api/projects/{id}.

These tests use the FastAPI TestClient and a fake_orchestrator fixture
so the routes are exercised end-to-end (including the
``Depends(get_orchestrator)`` indirection that previously caused
monkeypatching issues — see agent memory for the FastAPI singleton
gotcha).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.deps as _api_deps
from api.app import app
from api.routes import projects as _projects_routes


class FakeProject:
    def __init__(self):
        self.id = "p1"
        self.metadata: dict = {}
        self.runtime = type("R", (), {
            "coder_mode": "default",
            "coder_policy": None,
        })()


class FakeOrchestrator:
    def __init__(self):
        self.projects = {"p1": FakeProject()}

    def get_project(self, project_id: str):
        return self.projects.get(project_id)


@pytest.fixture
def fake_orchestrator():
    real_deps = _api_deps.orchestrator
    real_routes = _projects_routes._orch
    fake = FakeOrchestrator()
    _api_deps.orchestrator = fake
    _projects_routes._orch = lambda: fake
    try:
        yield fake
    finally:
        _api_deps.orchestrator = real_deps
        _projects_routes._orch = real_routes


@pytest.fixture
def client(fake_orchestrator):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_coder_mode_default(client, fake_orchestrator):
    r = client.get("/api/projects/p1/coder_mode")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == "p1"
    assert body["mode"] == "default"
    assert body["hint"] == ""


def test_get_coder_mode_unknown_project(client):
    r = client.get("/api/projects/nope/coder_mode")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------


def test_set_coder_mode_read_only(client, fake_orchestrator):
    r = client.post("/api/projects/p1/coder_mode", json={"mode": "read_only"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "read_only"
    assert body["project_id"] == "p1"
    proj = fake_orchestrator.projects["p1"]
    assert proj.metadata.get("coder_mode") == "read_only"
    assert proj.runtime.coder_mode == "read_only"


def test_set_coder_mode_sandbox(client, fake_orchestrator):
    r = client.post("/api/projects/p1/coder_mode", json={"mode": "sandbox"})
    assert r.status_code == 200
    assert r.json()["mode"] == "sandbox"
    assert fake_orchestrator.projects["p1"].runtime.coder_mode == "sandbox"


def test_set_coder_mode_default_roundtrip(client):
    """Default mode is also a valid value, not just read_only/sandbox."""
    r = client.post("/api/projects/p1/coder_mode", json={"mode": "default"})
    assert r.status_code == 200
    assert r.json()["mode"] == "default"


def test_set_coder_mode_garbage_falls_back_to_default(client):
    r = client.post("/api/projects/p1/coder_mode", json={"mode": "garbage-value"})
    assert r.status_code == 200
    assert r.json()["mode"] == "default"


def test_set_coder_mode_alias_readonly(client):
    r = client.post("/api/projects/p1/coder_mode", json={"mode": "ro"})
    assert r.status_code == 200
    assert r.json()["mode"] == "read_only"


def test_set_coder_mode_missing_mode_field(client):
    r = client.post("/api/projects/p1/coder_mode", json={})
    assert r.status_code == 200
    # no 'mode' ⇒ default
    assert r.json()["mode"] == "default"


def test_set_coder_mode_unknown_project(client):
    r = client.post("/api/projects/nope/coder_mode", json={"mode": "read_only"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Hint surfaces in the GET response
# ---------------------------------------------------------------------------


def test_get_coder_mode_returns_hint_for_read_only(client, fake_orchestrator):
    fake_orchestrator.projects["p1"].runtime.coder_mode = "read_only"
    r = client.get("/api/projects/p1/coder_mode")
    assert r.status_code == 200
    assert "read-only" in r.json()["hint"]


def test_get_coder_mode_returns_hint_for_sandbox(client, fake_orchestrator):
    fake_orchestrator.projects["p1"].runtime.coder_mode = "sandbox"
    r = client.get("/api/projects/p1/coder_mode")
    assert r.status_code == 200
    assert "worktree" in r.json()["hint"]

