"""Tests for the memory API endpoints."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Per-function scope so test state (projects, notes, …) doesn't
    # leak across tests in the same file. The settings.json is
    # also re-imported so the workspace_dir setting sticks.
    import importlib
    os.environ.setdefault("KAIROS_WORKSPACE", "./_api_test_workspace")
    # Force a fresh import so any test that mutated module-level
    # state (e.g. settings) starts clean.
    import api.deps as _api_deps
    importlib.reload(_api_deps)
    from api.app import app
    return TestClient(app)


def test_list_notes_endpoint(client):
    # Pick the first project; if there are none, create one.
    projects = client.get("/api/projects").json().get("projects", [])
    if not projects:
        resp = client.post("/api/projects", json={
            "name": "mem-api-test", "description": "tmp", "work_dir": "./_api_test_workspace",
        })
        created = resp.json()
        # When a project with the same name already exists, the API
        # can return a 409 with a non-`id` payload. Tolerate that by
        # falling back to the first existing project.
        if "id" not in created:
            projects = client.get("/api/projects").json().get("projects", [])
            if not projects:
                # Nothing in the store — surface the original error.
                raise AssertionError(
                    f"POST /api/projects returned {resp.status_code} "
                    f"without 'id': {created!r}"
                )
            pid = projects[0]["id"]
        else:
            pid = created["id"]
    else:
        pid = projects[0]["id"]
    r = client.get(f"/api/projects/{pid}/memory/notes")
    assert r.status_code == 200, r.text
    assert "notes" in r.json()


def test_memory_overview_endpoint(client):
    projects = client.get("/api/projects").json().get("projects", [])
    if not projects:
        pytest.skip("no projects available")
    pid = projects[0]["id"]
    r = client.get(f"/api/projects/{pid}/memory")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == pid
    assert "notes_count" in body
    assert "skills_count" in body


def test_add_note_then_list_round_trip(client):
    projects = client.get("/api/projects").json().get("projects", [])
    if not projects:
        pytest.skip("no projects available")
    pid = projects[0]["id"]
    r = client.post(f"/api/projects/{pid}/memory/notes", json={
        "kind": "fact", "title": "API test note", "body": "smoke body",
    })
    assert r.status_code == 200, r.text
    nid = r.json()["id"]
    listed = client.get(f"/api/projects/{pid}/memory/notes").json()["notes"]
    assert any(n["id"] == nid for n in listed)
    # Cleanup: delete the note we just added.
    client.delete(f"/api/projects/{pid}/memory/notes/{nid}")


def test_search_kb_endpoint(client):
    r = client.get("/api/projects/any/memory/kb", params={"query": "anything"})
    assert r.status_code == 200
    assert "insights" in r.json()


def test_health_includes_memory(client):
    projects = client.get("/api/projects").json().get("projects", [])
    if not projects:
        pytest.skip("no projects available")
    pid = projects[0]["id"]
    r = client.get(f"/api/projects/{pid}/health")
    assert r.status_code == 200
    body = r.json()
    # The health endpoint must include a "memory" block (the new
    # surface); even when nothing has been remembered yet it is present
    # with zero counts.
    assert "memory" in body
    assert "notes_count" in body["memory"]
    assert "skills_count" in body["memory"]
