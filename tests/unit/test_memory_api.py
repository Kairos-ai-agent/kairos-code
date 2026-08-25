"""Tests for the memory API endpoints."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Per-function scope so test state (projects, notes, …) doesn't
    # leak across tests in the same file. We rely on the app
    # being constructed once at import time (module-scope), but
    # we blow away the SQLite database between tests so each
    # one starts with an empty project list.
    import shutil
    from pathlib import Path
    os.environ.setdefault("KAIROS_WORKSPACE", "./_api_test_workspace")
    # Wipe the test DB so the project list starts empty.
    test_db = Path("data/_api_test.sqlite")
    if test_db.exists():
        try:
            test_db.unlink()
        except OSError:
            pass
    from api.app import app
    return TestClient(app)


def test_list_notes_endpoint(client):
    import uuid as _uuid
    # Pick the first project; if there are none, create one with a
    # unique name (the API's create_project path is sensitive to
    # name collisions; we don't depend on the specific response
    # shape, just on having at least one project).
    projects = client.get("/api/projects").json().get("projects", [])
    if not projects:
        unique_name = f"mem-api-test-{_uuid.uuid4().hex[:8]}"
        resp = client.post("/api/projects", json={
            "name": unique_name, "description": "tmp",
            "work_dir": "./_api_test_workspace",
        })
        assert resp.status_code == 200, resp.text
        created = resp.json()
        assert "id" in created, f"unexpected POST response: {created!r}"
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
    import uuid as _uuid
    unique_name = f"mem-api-test-{_uuid.uuid4().hex[:8]}"
    resp = client.post("/api/projects", json={
        "name": unique_name, "description": "tmp",
        "work_dir": "./_api_test_workspace",
    })
    assert resp.status_code == 200, resp.text
    pid = resp.json()["id"]
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
