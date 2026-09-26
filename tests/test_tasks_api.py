"""The tasks API: where a durable task, a background subagent and a job show up.

Before this, a `spawn_subagent(background=true)` handle was only reachable if
you already knew the handle, and a `.har/` task was only reachable from a
terminal. These tests pin the list/start/resume/history contract, and the
restart case: state lives on disk, so the API sees it again in a new process.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.deps as _api_deps
from api.app import app


class FakeSession:
    def __init__(self, round_no: int) -> None:
        self.session_id = "sess-" + str(round_no)
        self.history = [{"round": round_no,
                         "review": {"verdict": "approve", "score": 90,
                                    "approved": True},
                         "summary": "did round " + str(round_no)}]
        self.plan_text = "- do the thing\n"


class FakeProject:
    def __init__(self, root) -> None:
        self.id = "p1"
        self.work_dir = str(root)
        self.workspace = root
        self.loop_task = None
        self.loop_session = None


class FakeOrchestrator:
    def __init__(self, project) -> None:
        self.project = project
        self.rounds_started = 0

    def get_project(self, project_id):
        return self.project if project_id == self.project.id else None

    async def start_loop(self, project_id, requirement):
        self.rounds_started += 1
        self.project.loop_session = FakeSession(self.rounds_started)

        async def _finish():
            return None

        self.project.loop_task = asyncio.ensure_future(_finish())
        return "sess-" + str(self.rounds_started)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def fake_orch(workspace):
    real = _api_deps.orchestrator
    fake = FakeOrchestrator(FakeProject(workspace))
    _api_deps.orchestrator = fake
    try:
        yield fake
    finally:
        _api_deps.orchestrator = real


@pytest.fixture
def client(fake_orch):
    with TestClient(app) as c:
        yield c


def test_list_is_empty_for_a_fresh_project(client):
    body = client.get("/api/tasks/p1").json()
    assert body["project_id"] == "p1"
    assert body["durable"] is None
    assert body["subagents"] == [] and body["autonomous"] == []
    assert body["counts"]["durable"] == 0


def test_unknown_project_is_a_404(client):
    assert client.get("/api/tasks/nope").status_code == 404
    assert client.post("/api/tasks/nope", json={"goal": "x"}).status_code == 404


def test_start_then_list_then_history(client):
    start = client.post("/api/tasks/p1", json={"goal": "migrate the endpoints",
                                               "rounds_planned": 4})
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["ok"] and body["goal"] == "migrate the endpoints"
    assert body["round"] == 0

    listed = client.get("/api/tasks/p1").json()
    assert listed["durable"]["goal"] == "migrate the endpoints"
    assert listed["counts"]["durable"] == 1

    history = client.get("/api/tasks/p1/history").json()
    assert history["history"] == []


def test_a_second_start_is_refused(client):
    assert client.post("/api/tasks/p1", json={"goal": "first"}).status_code == 200
    second = client.post("/api/tasks/p1", json={"goal": "second"})
    assert second.status_code == 400
    assert "already exists" in second.json()["detail"]
    # The first task is untouched.
    assert client.get("/api/tasks/p1").json()["durable"]["goal"] == "first"


def test_start_requires_a_goal(client):
    assert client.post("/api/tasks/p1", json={"goal": "  "}).status_code == 400


def test_resume_runs_a_tick_and_records_it(client, fake_orch):
    client.post("/api/tasks/p1", json={"goal": "migrate the endpoints"})
    res = client.post("/api/tasks/p1/resume", json={"ticks": 1})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    # The fake loop approves on its first round, so the state layer stops with 4.
    assert body["code"] == 4, body
    assert fake_orch.rounds_started == 1
    assert body["status"]["round"] == 1
    assert body["status"]["last_approve"] is True

    history = client.get("/api/tasks/p1/history").json()["history"]
    assert len(history) == 1 and history[0]["approved"] is True


def test_resume_without_a_task_is_a_404(client):
    assert client.post("/api/tasks/p1/resume", json={"ticks": 1}).status_code == 404


def test_history_without_a_task_is_a_404(client):
    assert client.get("/api/tasks/p1/history").status_code == 404


def test_the_state_survives_a_new_orchestrator(client, fake_orch, workspace):
    """The API, not the object in memory, is the source of truth."""
    client.post("/api/tasks/p1", json={"goal": "migrate the endpoints"})
    client.post("/api/tasks/p1/resume", json={"ticks": 1})

    from kairos import durable

    revived = FakeProject(workspace)
    state = durable.status(revived)
    assert state is not None
    assert state["round"] == 1
    assert state["goal"] == "migrate the endpoints"
