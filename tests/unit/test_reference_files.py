"""Unit tests for the project reference files feature.

Covers persistence CRUD, the orchestrator's digest builder, and the
HTTP upload/list/delete endpoints. Uses pytest's `tmp_path` fixture so
SQLite files clean up cleanly on Windows (no leaked connections).
"""

from __future__ import annotations

import pytest
from pathlib import Path

@pytest.fixture
def db(tmp_path):
    """Fresh Persistence rooted in tmp_path."""
    from kairos.core.persistence import Persistence
    return Persistence(tmp_path / "ref_test.db")

# ---------------------------------------------------------------- persistence

def test_add_and_list_file(db):
    db.add_file("f1", "p1", "design.md", "text/markdown", 1024, "# Design\n\nBody")
    db.add_file("f2", "p1", "spec.pdf", "application/pdf", 999, "%PDF-1.4...")
    files = db.list_files("p1")
    assert len(files) == 2
    names = {f["name"] for f in files}
    assert names == {"design.md", "spec.pdf"}
    # `content` is not in list — only metadata.
    assert "content" not in files[0]

def test_load_file_includes_content(db):
    db.add_file("f1", "p1", "x.txt", "text/plain", 5, "hello")
    record = db.load_file("f1")
    assert record is not None
    assert record["content"] == "hello"
    assert record["name"] == "x.txt"

def test_delete_file(db):
    db.add_file("f1", "p1", "x.txt", "text/plain", 5, "hello")
    assert db.delete_file("f1") is True
    assert db.load_file("f1") is None
    assert db.delete_file("f1") is False  # idempotent

def test_delete_project_cascades_to_files(db):
    db.add_file("f1", "p1", "a.txt", "text/plain", 1, "a")
    db.add_file("f2", "p1", "b.txt", "text/plain", 1, "b")
    db.add_file("f3", "p2", "c.txt", "text/plain", 1, "c")
    db.delete_project("p1")
    assert db.list_files("p1") == []
    # p2's file untouched
    assert len(db.list_files("p2")) == 1

def test_load_all_for_project_includes_content(db):
    db.add_file("f1", "p1", "small.txt", "text/plain", 5, "hello")
    files = db.load_all_files_for_project("p1")
    assert len(files) == 1
    assert files[0]["content"] == "hello"

# ---------------------------------------------------------------- digest builder

def test_build_reference_digest_inlines_small_files(db):
    """Files under the inline threshold should appear in the digest
    with their full text. Larger files get a preview only."""
    from kairos.core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)  # skip __init__
    orch._db = db
    orch._projects = {}

    # Small file: full content inlined.
    db.add_file("f1", "p1", "small.md", "text/markdown",
                30, "Tiny but complete body.")
    # Large file: only preview.
    big = "A" * 10_000
    db.add_file("f2", "p1", "big.txt", "text/plain", len(big), big)

    digest = orch.build_reference_digest("p1")
    assert "## 参考资料" in digest
    assert "small.md" in digest
    assert "Tiny but complete body." in digest
    assert "big.txt" in digest
    # Large file should be truncated, not the full 10k chars.
    assert "A" * 10_000 not in digest
    assert "truncated" in digest.lower() or "…" in digest

def test_build_reference_digest_empty_for_no_files(db):
    from kairos.core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch._db = db
    orch._projects = {}
    assert orch.build_reference_digest("p1") == ""

def test_project_to_dict_includes_files(db):
    """to_dict() should expose a `files` array of metadata so the UI
    doesn't need a second round-trip to render the list."""
    from kairos.core.orchestrator import Project
    project = Project("p1", "T", "d", Path("."), db=db)
    db.add_file("f1", "p1", "x.txt", "text/plain", 1, "x")
    d = project.to_dict()
    assert "files" in d
    assert len(d["files"]) == 1
    assert d["files"][0]["name"] == "x.txt"

# ---------------------------------------------------------------- API endpoints

def _make_orch_with_api(tmp_path):
    """Build a real Orchestrator backed by a temp DB + a FastAPI app
    that has the projects router mounted. Returns (client, orch).

    The route module imports `orchestrator` from api.deps at import time
    (e.g. `from api.deps import orchestrator`), so just rebinding
    `api.deps.orchestrator` doesn't help. We patch the symbol on the
    route module directly, which is what FastAPI sees at request time.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kairos.core.orchestrator import Orchestrator
    from kairos.llm.model_router import ModelRouter
    from kairos.config.settings import settings
    from api.routes import projects as projects_route_module

    # CRITICAL: point Orchestrator at a temp DB instead of the real
    # `data/kairos.db`. The default Orchestrator() reads
    # settings.data_dir, which would leak test rows into the user's DB.
    settings.data_dir = tmp_path

    ws = tmp_path / "ws"
    ws.mkdir()
    orch = Orchestrator(model_router=ModelRouter(), workspace_base=ws)

    # Patch the symbol the route module already imported.
    projects_route_module.orchestrator = orch
    import api.deps as deps
    deps.orchestrator = orch  # belt-and-suspenders

    app = FastAPI()
    app.include_router(projects_route_module.router, prefix="/api/projects")
    return TestClient(app), orch

def test_upload_file_via_api(tmp_path):
    client, orch = _make_orch_with_api(tmp_path)
    project = orch.create_project("Test", "x", work_dir=str(tmp_path / "ws"))

    files = {"file": ("hello.txt", b"hello world", "text/plain")}
    r = client.post(f"/api/projects/{project.id}/files", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok"
    assert data["file"]["name"] == "hello.txt"
    assert data["file"]["size"] == len(b"hello world")
    file_id = data["file"]["id"]

    # List files.
    r = client.get(f"/api/projects/{project.id}/files")
    assert r.status_code == 200
    assert len(r.json()["files"]) == 1
    assert r.json()["files"][0]["id"] == file_id

    # Delete.
    r = client.delete(f"/api/projects/{project.id}/files/{file_id}")
    assert r.status_code == 200
    # And list is empty.
    r = client.get(f"/api/projects/{project.id}/files")
    assert r.json()["files"] == []

def test_upload_rejects_oversized_file(tmp_path):
    client, orch = _make_orch_with_api(tmp_path)
    project = orch.create_project("Test", "x", work_dir=str(tmp_path / "ws"))

    # 6 MB > 5 MB cap
    big = b"x" * (6 * 1024 * 1024)
    r = client.post(f"/api/projects/{project.id}/files",
                    files={"file": ("big.bin", big, "application/octet-stream")})
    assert r.status_code == 413
    assert "too large" in r.json()["detail"].lower()

def test_delete_file_returns_404_for_unknown(tmp_path):
    client, orch = _make_orch_with_api(tmp_path)
    project = orch.create_project("Test", "x", work_dir=str(tmp_path / "ws"))

    r = client.delete(f"/api/projects/{project.id}/files/nonexistent_id")
    assert r.status_code == 404

def test_to_dict_round_trip_after_upload(tmp_path):
    """to_dict's `files` field should reflect a freshly uploaded file."""
    client, orch = _make_orch_with_api(tmp_path)
    project = orch.create_project("Test", "x", work_dir=str(tmp_path / "ws"))

    client.post(f"/api/projects/{project.id}/files",
                files={"file": ("datasheet.pdf", b"%PDF-1.4", "application/pdf")})

    # to_dict pulls from DB, not from in-memory list, so this is a real
    # integration test of the file → DB → to_dict path.
    p = orch.get_project(project.id)
    d = p.to_dict()
    assert any(f["name"] == "datasheet.pdf" for f in d["files"])
