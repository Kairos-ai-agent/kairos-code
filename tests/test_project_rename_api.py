"""R38.10 — renaming a project from the sidebar.

Same shape as the other route tests: a fake orchestrator behind
``_projects_routes._orch``, so the route runs end to end and the write is
asserted against a recording "database".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.routes import projects as _projects_routes


class FakeDb:
    def __init__(self):
        self.saved = []

    def save_project(self, project):
        self.saved.append((project.id, project.name, project.description))


class FakeProject:
    def __init__(self, pid="p1", name="未命名"):
        self.id = pid
        self.name = name
        self.description = ""
        self.metadata = {}


class FakeOrchestrator:
    def __init__(self):
        self._db = FakeDb()
        self.projects = {"p1": FakeProject()}

    def get_project(self, project_id):
        return self.projects.get(project_id)


@pytest.fixture
def orch():
    real = _projects_routes._orch
    fake = FakeOrchestrator()
    _projects_routes._orch = lambda: fake
    try:
        yield fake
    finally:
        _projects_routes._orch = real


@pytest.fixture
def client():
    return TestClient(app)


def test_rename_updates_the_name_and_persists_it(client, orch):
    r = client.patch("/api/projects/p1", json={"name": "  抽卡拉到最底  "})
    assert r.status_code == 200
    assert r.json()["name"] == "抽卡拉到最底"           # trimmed
    assert orch.projects["p1"].name == "抽卡拉到最底"
    # Written through, not just held in memory.
    assert orch._db.saved == [("p1", "抽卡拉到最底", "")]


def test_an_untitled_project_can_be_renamed(client, orch):
    assert orch.projects["p1"].name == "未命名"
    r = client.patch("/api/projects/p1", json={"name": "正经名字"})
    assert r.status_code == 200
    assert orch.projects["p1"].name == "正经名字"


@pytest.mark.parametrize("payload", [{"name": ""}, {"name": "   "}, {"name": None}])
def test_empty_names_are_refused(client, orch, payload):
    r = client.patch("/api/projects/p1", json=payload)
    assert r.status_code == 400
    assert orch.projects["p1"].name == "未命名"          # untouched
    assert orch._db.saved == []                          # and nothing written


def test_a_too_long_name_is_refused(client, orch):
    r = client.patch("/api/projects/p1", json={"name": "x" * 121})
    assert r.status_code == 400
    assert orch.projects["p1"].name == "未命名"


def test_unknown_project_is_a_404(client, orch):
    assert client.patch("/api/projects/nope", json={"name": "x"}).status_code == 404


def test_an_empty_patch_is_refused(client, orch):
    assert client.patch("/api/projects/p1", json={}).status_code == 400


def test_the_description_can_be_updated_too(client, orch):
    r = client.patch("/api/projects/p1", json={"description": "说明"})
    assert r.status_code == 200
    assert r.json()["description"] == "说明"
    assert orch._db.saved[-1][2] == "说明"
