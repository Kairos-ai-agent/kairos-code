"""Tests for the three backend routes the frontend already calls.

  * ``GET  /api/projects/{id}/stats``               (Loop.tsx ``interface Stats``)
  * ``GET  /api/projects/{id}/plan/visualization``  (Loop.tsx ``interface PlanViz``)
  * ``POST /api/projects/{id}/requirements``        (Project.tsx autosave)

Design notes:

  * Every route is asserted to exist in ``app.openapi()["paths"]`` with the
    right HTTP method. Calling the handler function directly would miss a
    mount/prefix mistake — exactly the class of bug that left the checkpoint
    panel blank for four rounds (see ``test_frontend_route_parity``).
  * These are fully offline: a fake orchestrator is swapped into
    ``api.deps.orchestrator`` (the shim every handler reads), the file-tree
    part runs against a ``tmp_path`` project root, and no server is started.
"""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

import api.deps as _api_deps
from api.app import app
from api.routes.projects import _PLAN_VIZ_MAX_TREE_ENTRIES
from kairos.loop.plan import Plan, TodoItem
from kairos.review.plan_viz import (
    entries_to_tree_text,
    escape_mermaid_label,
    plan_text_to_mermaid,
    todos_to_mermaid,
)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeProject:
    def __init__(self, root, pid: str = "p1") -> None:
        self.id = pid
        self.name = "proj"
        self.work_dir = str(root)
        self.workspace = root
        self.loop_task = None
        self.loop_session = None
        self.requirements = ""
        self.metadata: dict = {}

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "requirements": self.requirements}


class FakeDb:
    def __init__(self) -> None:
        self.saved: list = []

    def save_project(self, project) -> None:
        self.saved.append(project)


class FakeOrch:
    """Only what the three routes touch."""

    def __init__(self, project: FakeProject, plan=None) -> None:
        self.project = project
        self._plan = plan
        self._db = FakeDb()

    def get_project(self, project_id: str):
        return self.project if project_id == self.project.id else None

    def get_plan(self, project_id: str):
        return self._plan


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Return (client, fake_project, fake_orch) with the fake wired in."""
    real = _api_deps.orchestrator

    def _make(plan=None, pid: str = "p1"):
        project = FakeProject(tmp_path, pid)
        fake = FakeOrch(project, plan)
        monkeypatch.setattr(_api_deps, "orchestrator", fake)
        return TestClient(app), project, fake

    yield _make
    _api_deps.orchestrator = real


def _session(**kw):
    """A LoopSession-shaped namespace with the fields /stats reads."""
    base = dict(history=[], score_window=[], total_tokens_used=0,
                infra_failure_streak=0, no_progress_count=0, round=0,
                plan_todos=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# ① routes must be real, mounted routes (the checkpoint-panel lesson)
# ---------------------------------------------------------------------------


def test_three_routes_are_in_the_openapi_path_table():
    paths = app.openapi()["paths"]
    assert "/api/projects/{project_id}/stats" in paths
    assert "get" in paths["/api/projects/{project_id}/stats"]

    assert "/api/projects/{project_id}/plan/visualization" in paths
    assert "get" in paths["/api/projects/{project_id}/plan/visualization"]

    assert "/api/projects/{project_id}/requirements" in paths
    assert "post" in paths["/api/projects/{project_id}/requirements"]


# ---------------------------------------------------------------------------
# ② /stats — full contract from a live session
# ---------------------------------------------------------------------------


def _stats_session():
    return _session(
        history=[
            {"round": 1,
             "review": {"score": 70, "approve": False,
                        "issues": [{"severity": "MAJOR"}, {"severity": "MINOR"}],
                        "summary": "2 bugs"},
             "coder": "...", "plan": None},
            # regression rollback row — NOT a review round
            {"round": 2, "rollback": True, "reason": "score regressed", "plan": None},
            {"round": 3,
             "review": {"score": 90, "approve": True, "issues": [],
                        "summary": "clean"},
             "coder": "...", "plan": None},
        ],
        score_window=[70, 90],
        total_tokens_used=1234,
        infra_failure_streak=2,
        no_progress_count=3,
        round=3,
    )


def test_stats_has_every_contract_field_with_correct_types(make_client):
    client, project, _ = make_client()
    project.loop_session = _stats_session()

    r = client.get("/api/projects/p1/stats")
    assert r.status_code == 200, r.text
    body = r.json()

    # Exactly the Loop.tsx `interface Stats` keys.
    assert set(body) == {
        "running", "rounds", "score_window", "total_tokens_used",
        "approximate_cost_usd", "infra_failure_streak", "no_progress_count",
    }
    assert isinstance(body["running"], bool)
    assert isinstance(body["rounds"], list)
    assert isinstance(body["score_window"], list)
    assert isinstance(body["total_tokens_used"], int)
    assert isinstance(body["approximate_cost_usd"], float)
    assert isinstance(body["infra_failure_streak"], int)
    assert isinstance(body["no_progress_count"], int)

    assert body["running"] is False
    assert body["score_window"] == [70, 90]
    assert body["total_tokens_used"] == 1234
    # Honest sentinel: the ledger has no per-project attribution.
    assert body["approximate_cost_usd"] == 0.0
    assert body["infra_failure_streak"] == 2
    assert body["no_progress_count"] == 3


def test_stats_maps_history_and_drops_rollback_rows(make_client):
    client, project, _ = make_client()
    project.loop_session = _stats_session()

    rounds = client.get("/api/projects/p1/stats").json()["rounds"]
    # The rollback row (round 2) is not a review and must not appear.
    assert [r["round"] for r in rounds] == [1, 3]
    assert all(r["round"] != 2 for r in rounds)

    first = rounds[0]
    assert set(first) == {"round", "score", "approve", "issues", "summary", "ts"}
    assert first == {"round": 1, "score": 70, "approve": False, "issues": 2,
                     "summary": "2 bugs", "ts": 0.0}
    # An approved review must not be reported as a failure.
    assert rounds[1]["approve"] is True
    assert rounds[1]["score"] == 90
    assert rounds[1]["issues"] == 0


def test_stats_404_without_a_loop_session(make_client):
    client, _project, _ = make_client()
    r = client.get("/api/projects/p1/stats")
    assert r.status_code == 404


def test_stats_404_for_unknown_project(make_client):
    client, _project, _ = make_client()
    assert client.get("/api/projects/nope/stats").status_code == 404


# ---------------------------------------------------------------------------
# ③ /plan/visualization — structured todos
# ---------------------------------------------------------------------------


def test_plan_viz_from_structured_todos(make_client):
    plan = Plan(todos=[
        TodoItem(status="completed", content="Read README"),
        TodoItem(status="in_progress", content="Add CSV reader"),
        TodoItem(status="pending", content="Write tests"),
    ])
    session = _session(plan_todos=plan, round=4)
    client, project, _ = make_client(
        plan={"pending": True, "decision": None, "text": "", "round": 4})
    project.loop_session = session

    r = client.get("/api/projects/p1/plan/visualization")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"mermaid", "file_tree", "round"}
    assert body["round"] == 4

    mm = body["mermaid"]
    assert mm.startswith("flowchart TD")
    for text in ("Read README", "Add CSV reader", "Write tests"):
        assert text in mm
    # status colouring present
    assert "classDef completed" in mm
    assert "classDef in_progress" in mm
    assert "classDef pending" in mm
    # sequential edges between the three nodes
    assert "n0 --> n1" in mm and "n1 --> n2" in mm


def test_plan_viz_404_when_there_is_nothing_to_draw(make_client):
    client, project, _ = make_client(plan={"pending": False, "decision": None,
                                           "text": "", "round": 0})
    project.loop_session = _session(plan_todos=None)
    assert client.get("/api/projects/p1/plan/visualization").status_code == 404


# ---------------------------------------------------------------------------
# ④ hostile input must not break the mermaid structure
# ---------------------------------------------------------------------------


_HOSTILE = 'bad"quote\nsecond[line] --> inject<script>#&"\'\'\ntrailing'


def _assert_mermaid_wellformed(mermaid: str):
    lines = mermaid.split("\n")
    assert lines[0] == "flowchart TD"
    quoted = [ln for ln in lines if '["' in ln]
    assert quoted, "expected at least one labelled node"
    for ln in quoted:
        # a node line is  nN["..."]  with the inner quote escaped — exactly
        # two literal double-quotes (the wrapping pair).
        assert ln.count('"') == 2, f"unescaped quote in node line: {ln!r}"
        assert ln.rstrip().endswith('"]'), ln
        assert "\n" not in ln
        assert "\r" not in ln
    # every arrow is a bare edge, never part of a label
    for ln in lines:
        if "-->" in ln:
            stripped = ln.strip()
            assert stripped.endswith("--> " + stripped.split("--> ")[-1])
            assert stripped.split("-->")[0].strip().startswith("n")


def test_mermaid_structured_todos_survive_hostile_content(make_client):
    plan = Plan(todos=[
        TodoItem(status="pending", content=_HOSTILE),
        TodoItem(status="completed", content="ok"),
    ])
    client, project, _ = make_client(
        plan={"pending": True, "decision": None, "text": "", "round": 1})
    project.loop_session = _session(plan_todos=plan, round=1)

    mm = client.get("/api/projects/p1/plan/visualization").json()["mermaid"]
    _assert_mermaid_wellformed(mm)
    # hostiles were escaped, not dropped
    assert "#quot;" in mm          # the "
    assert "#35;" in mm            # the #
    assert "&lt;" in mm and "&gt;" in mm   # the </> signs
    assert "&amp;" in mm           # the &
    assert "<br/>" in mm           # the newline
    # the injected fake edge is neutered — the only real arrow is n0 --> n1
    assert "n0 --> n1" in mm
    assert "-->" not in mm.replace("n0 --> n1", "")


def test_escape_mermaid_label_handles_every_kind_of_hostile_text():
    out = escape_mermaid_label('a"b\nc<d>e#f&g --> h')
    assert "\n" not in out
    assert '"' not in out
    assert out == 'a#quot;b<br/>c&lt;d&gt;e#35;f&amp;g --&gt; h'


def test_text_fallback_is_declared_as_a_rendering_not_a_generated_graph(make_client):
    text = '1. do the "first" thing\n2. 中文步骤 --> 完成\n- bullet item'
    client, project, _ = make_client(
        plan={"pending": False, "decision": None, "text": text, "round": 2})
    project.loop_session = _session(plan_todos=None, round=2)

    body = client.get("/api/projects/p1/plan/visualization").json()
    mm = body["mermaid"]
    assert body["round"] == 2
    # the provenance comment is explicit: this is the plan text, not a graph
    assert "rendering of the plan text" in mm.split("\n")[1]
    _assert_mermaid_wellformed(mm)
    assert "中文步骤 --&gt; 完成" in mm
    assert "bullet item" in mm
    # three top-level items → three nodes
    assert mm.count('["') == 3


def test_plan_text_to_mermaid_returns_empty_for_blank_text():
    assert plan_text_to_mermaid("") == ""
    assert plan_text_to_mermaid("   \n\n  ") == ""


# ---------------------------------------------------------------------------
# ⑤ the embedded file tree is bounded
# ---------------------------------------------------------------------------


def test_file_tree_is_capped_for_a_large_directory(make_client):
    client, project, _ = make_client(
        plan={"pending": True, "decision": None, "text": "", "round": 1})
    project.loop_session = _session(plan_todos=Plan(todos=[
        TodoItem(status="pending", content="x")]), round=1)

    # A directory far larger than the cap.
    n = _PLAN_VIZ_MAX_TREE_ENTRIES + 50
    for i in range(n):
        (project.workspace / f"file_{i:04d}.txt").write_text("x", encoding="utf-8")

    body = client.get("/api/projects/p1/plan/visualization").json()
    lines = body["file_tree"].split("\n")
    # capped rows + one truncation footer
    assert len(lines) <= _PLAN_VIZ_MAX_TREE_ENTRIES + 1
    assert lines[-1].startswith("... (+")
    assert "more entries, truncated" in lines[-1]


def test_entries_to_tree_text_uses_path_depth_for_indentation():
    entries = [
        types.SimpleNamespace(path="src", name="src", is_dir=True),
        types.SimpleNamespace(path="src/app.py", name="app.py", is_dir=False),
        types.SimpleNamespace(path="README.md", name="README.md", is_dir=False),
    ]
    out = entries_to_tree_text(entries)
    assert out.split("\n") == ["src/", "  app.py", "README.md"]


# ---------------------------------------------------------------------------
# ⑥ POST /requirements — the write actually lands
# ---------------------------------------------------------------------------


def test_requirements_are_persisted(make_client):
    client, project, fake = make_client()

    r = client.post("/api/projects/p1/requirements",
                    json={"requirements": "build a CSV reader"})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "saved", "project_id": "p1",
                        "requirements": "build a CSV reader"}

    # persisted onto the object AND saved through the db
    assert fake.get_project("p1").requirements == "build a CSV reader"
    assert fake._db.saved == [project]
    # …and it round-trips into the project list the frontend reads
    assert project.to_dict()["requirements"] == "build a CSV reader"


def test_requirements_rejects_non_string(make_client):
    client, _project, _ = make_client()
    r = client.post("/api/projects/p1/requirements", json={"requirements": 123})
    assert r.status_code == 400


def test_requirements_rejects_oversized(make_client):
    client, _project, _ = make_client()
    r = client.post("/api/projects/p1/requirements",
                    json={"requirements": "x" * 100_001})
    assert r.status_code == 400


def test_requirements_404_for_unknown_project(make_client):
    client, _project, _ = make_client()
    r = client.post("/api/projects/nope/requirements",
                    json={"requirements": "x"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# unit: todos_to_mermaid honours the empty case
# ---------------------------------------------------------------------------


def test_todos_to_mermaid_empty_returns_blank():
    assert todos_to_mermaid([]) == ""
    assert todos_to_mermaid(None) == ""
