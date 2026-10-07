"""The product entry: ``POST /api/projects/{id}/start`` now routes.

Two things must both be true, and the whole point of this file is to hold them
at once:

* a request that declares (or clearly *is*) a non-code task runs on the
  domain-neutral skeleton, and its structured ``Verdict`` comes back in the
  response and is written to a run record;
* **an ordinary code request takes the Coder <-> Reviewer loop exactly as
  before** -- the skeleton is not on its path. This is the reverse proof: the
  test asserts the orchestrator's ``start_loop`` was called (and the skeleton
  was not), so a future refactor that quietly reroutes code work fails here.

Everything uses a fake generator: no network, no real model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.deps as _api_deps
from api.app import app
from api.routes import projects as _projects_routes


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeProject:
    def __init__(self, root: Path) -> None:
        self.id = "p1"
        self.work_dir = str(root)
        self.workspace = root
        self.loop_task = None
        self.loop_session = None
        self.metadata: dict = {}
        self.runtime = type("R", (), {"coder_mode": "default", "coder_policy": None})()


class FakeOrchestrator:
    """Enough of the Orchestrator: get_project, a bus, and a counted start_loop."""

    def __init__(self, project: FakeProject) -> None:
        from kairos.core.message_bus import MessageBus

        self.project = project
        self.message_bus = MessageBus()
        self.start_loop_calls: list = []

    def get_project(self, project_id: str):
        return self.project if project_id == self.project.id else None

    async def start_loop(self, project_id: str, requirement: str) -> str:
        self.start_loop_calls.append((project_id, requirement))
        return "sess-1"


def _fake_generate(prompt: str) -> str:
    """Deterministic model stand-in: cite every input, then self-check."""
    names = re.findall(r"INPUT: (\S+)", prompt or "")
    rows = [f"- 结论 {i + 1}，依据 [[{name}]]" for i, name in enumerate(names)]
    return "\n".join([
        "# 对比报告", "", "## 对比", *rows, "",
        "## 自检引用",
        f"SELF_CHECK: citations={len(names)}/{len(names)} ok",
    ])


@pytest.fixture
def fake_gen(monkeypatch):
    """Inject the fake generator at the service seam (no model, no network)."""
    monkeypatch.setattr("kairos.skeleton.service.default_generator", lambda: _fake_generate)


@pytest.fixture
def docs_root(tmp_path) -> Path:
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "alpha.md").write_text("方案甲：延迟 30ms", encoding="utf-8")
    (root / "docs" / "beta.md").write_text("方案乙：延迟 80ms", encoding="utf-8")
    (root / "docs" / "gamma.md").write_text("方案丙：延迟 15ms", encoding="utf-8")
    return root


@pytest.fixture
def code_root(tmp_path) -> Path:
    root = tmp_path / "code"
    (root / "pkg").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return root


@pytest.fixture
def make_client(fake_gen):
    """Build a TestClient whose project root is ``root``; restores globals."""
    real_deps = _api_deps.orchestrator
    real_routes = _projects_routes._orch
    clients: list = []

    def _build(root: Path):
        fake = FakeOrchestrator(FakeProject(root))
        _api_deps.orchestrator = fake
        _projects_routes._orch = lambda: fake
        c = TestClient(app)
        b = c.__enter__()
        clients.append((c, b, fake))
        return c, fake

    try:
        yield _build
    finally:
        for c, _b, _fake in clients:
            try:
                c.__exit__(None, None, None)
            except Exception:
                pass
        _api_deps.orchestrator = real_deps
        _projects_routes._orch = real_routes


# ---------------------------------------------------------------------------
# (1) explicit non-code task -> skeleton, Verdict visible in response + record
# ---------------------------------------------------------------------------

def test_explicit_docs_task_routes_to_the_skeleton(make_client, docs_root):
    client, fake = make_client(docs_root)

    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读三份文档，产出一页对比报告并自检引用。",
                          "kind": "docs"})
    assert r.status_code == 200, r.text
    body = r.json()

    # the response *says* which path it took
    assert body["route"] == "skeleton"
    assert body["workspace_kind"] == "docs"
    assert body["route_source"] == "explicit"

    # ...and carries the structured Verdict, not just a word
    verdict = body["verdict"]
    assert verdict["passed"] is True
    assert verdict["verifier"] == "citations"
    assert isinstance(verdict["evidence"], list) and verdict["evidence"]
    assert all(isinstance(e, dict) and "satisfied" in e for e in verdict["evidence"])
    assert body["outcome"] == "passed"

    # the loop was NOT used
    assert fake.start_loop_calls == []

    # the deliverables exist on disk
    assert (docs_root / "outputs" / "report.md").is_file()
    report = (docs_root / "outputs" / "report.md").read_text(encoding="utf-8")
    for name in ("alpha.md", "beta.md", "gamma.md"):
        assert f"[[{name}]]" in report


def test_routed_run_is_written_to_a_run_record(make_client, docs_root):
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读文档写对比报告", "kind": "docs"})
    assert r.status_code == 200, r.text
    body = r.json()

    run_file = Path(body["run_file"])
    assert run_file.is_file(), body["run_file"]
    assert run_file.parent == docs_root / ".kairos" / "skeleton-runs"

    record = json.loads(run_file.read_text(encoding="utf-8"))
    # the SAME structured verdict is in the persisted record
    assert record["outcome"] == "passed"
    assert record["verdict"] == body["verdict"]
    assert record["run_id"] == body["run_id"]


def test_routed_run_is_published_to_the_existing_bus(make_client, docs_root):
    """The existing observation path (project bus -> DB) sees the run."""
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读文档写对比报告", "kind": "docs"})
    assert r.status_code == 200, r.text

    topics = {m.topic for m in fake.message_bus.get_history(limit=100)}
    assert "skeleton.run.verified" in topics
    assert "skeleton.run.completed" in topics


def test_workspace_kind_alias_also_routes(make_client, docs_root):
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "写报告", "workspace_kind": "docs"})
    assert r.status_code == 200, r.text
    assert r.json()["route"] == "skeleton"
    assert fake.start_loop_calls == []


def test_explicit_docs_beats_a_code_workspace(make_client, code_root):
    """The caller's explicit signal wins even over a code-looking root."""
    client, fake = make_client(code_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "读文档写报告", "kind": "docs"})
    assert r.status_code == 200, r.text
    assert r.json()["route"] == "skeleton"
    assert fake.start_loop_calls == []


def test_heuristic_routes_a_docs_workspace_with_no_signal(make_client, docs_root):
    """No explicit kind, but the workspace is clearly a document set."""
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start", json={"requirement": "读文档写报告"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"] == "skeleton"
    assert body["route_source"] == "heuristic"
    assert fake.start_loop_calls == []


# ---------------------------------------------------------------------------
# (2) THE REVERSE PROOF: an ordinary code task still goes to the loop
# ---------------------------------------------------------------------------

def test_ordinary_code_task_still_takes_the_loop(make_client, code_root):
    client, fake = make_client(code_root)

    r = client.post("/api/projects/p1/start",
                    json={"requirement": "fix the auth bug"})
    assert r.status_code == 200, r.text
    body = r.json()

    # the loop ran, with the requirement, and returned a session id...
    assert fake.start_loop_calls == [("p1", "fix the auth bug")]
    assert body["session_id"] == "sess-1"
    # ...and the skeleton did not touch this request
    assert body.get("route") != "skeleton"
    assert "verdict" not in body


def test_no_signal_on_an_undecidable_workspace_stays_on_the_loop(make_client, tmp_path):
    """An empty workspace is undecided -> the loop, exactly as before."""
    empty = tmp_path / "empty"
    empty.mkdir()
    client, fake = make_client(empty)

    r = client.post("/api/projects/p1/start", json={"requirement": "do something"})
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == "sess-1"
    assert fake.start_loop_calls == [("p1", "do something")]


def test_explicit_repo_kind_on_a_docs_workspace_takes_the_loop(make_client, docs_root):
    """An explicit code signal keeps the loop even for a docs-looking root."""
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "refactor the module", "kind": "repo"})
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == "sess-1"
    assert fake.start_loop_calls == [("p1", "refactor the module")]


def test_already_running_loop_is_still_a_409(make_client, code_root):
    client, fake = make_client(code_root)

    class _Done:
        def done(self):
            return False

    fake.project.loop_task = _Done()
    r = client.post("/api/projects/p1/start", json={"requirement": "x"})
    assert r.status_code == 409
    assert fake.start_loop_calls == []


# ---------------------------------------------------------------------------
# (3) honest failure when the route needs a model and there is none
# ---------------------------------------------------------------------------

def test_skeleton_route_without_a_model_is_a_503(make_client, docs_root, monkeypatch):
    monkeypatch.setattr("kairos.skeleton.service.default_generator", lambda: None)
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/p1/start",
                    json={"requirement": "写报告", "kind": "docs"})
    assert r.status_code == 503, r.text
    assert "skeleton route failed" in r.json()["detail"]
    assert fake.start_loop_calls == []


def test_unknown_project_is_still_a_404(make_client, docs_root):
    client, fake = make_client(docs_root)
    r = client.post("/api/projects/nope/start", json={"requirement": "x", "kind": "docs"})
    assert r.status_code == 404
