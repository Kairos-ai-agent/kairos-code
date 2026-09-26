"""Artifacts: the things a run produces, kept where a person can read them.

Three layers, tested as three: the rows (kairos/core/persistence.py), the service
that the loop and the tools write through (kairos/artifacts.py), and the routes
the UI reads. The headline property is the last one — a screenshot the browser
tool takes becomes an artifact, is listed, and can be commented on.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.deps as _api_deps
from kairos import artifacts
from kairos.core.persistence import Persistence


@pytest.fixture
def db(tmp_path):
    return Persistence(db_path=tmp_path / "kairos.db")


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

def test_an_artifact_round_trips(db):
    db.add_artifact({"id": "a1", "project_id": "p1", "kind": "plan",
                     "title": "Round 1 plan", "body": "- do the thing",
                     "round_no": 1, "meta": {"chars": 14}})

    found = db.get_artifact("a1")
    assert found["project_id"] == "p1"
    assert found["kind"] == "plan"
    assert found["body"] == "- do the thing"
    assert found["round_no"] == 1
    assert found["meta"] == {"chars": 14}, "meta is stored as text, read as a dict"


def test_listing_is_newest_first_and_filterable(db):
    for i, kind in enumerate(["plan", "report", "plan"], start=1):
        db.add_artifact({"id": "a%d" % i, "project_id": "p1", "kind": kind,
                         "title": "t%d" % i, "created_at": 100.0 + i})

    every = db.list_artifacts("p1")
    assert [a["id"] for a in every] == ["a3", "a2", "a1"]

    plans = db.list_artifacts("p1", kind="plan")
    assert [a["id"] for a in plans] == ["a3", "a1"]


def test_another_project_is_not_mixed_in(db):
    db.add_artifact({"id": "a1", "project_id": "p1", "kind": "plan", "title": "mine"})
    db.add_artifact({"id": "a2", "project_id": "p2", "kind": "plan", "title": "theirs"})
    assert [a["id"] for a in db.list_artifacts("p1")] == ["a1"]


def test_an_unknown_artifact_is_none(db):
    assert db.get_artifact("nope") is None
    assert db.delete_artifact("nope") is False


def test_deleting_takes_the_thread_with_it(db):
    db.add_artifact({"id": "a1", "project_id": "p1", "kind": "note", "title": "n"})
    db.add_artifact_comment("a1", "user", "look at this")

    assert db.delete_artifact("a1") is True
    assert db.get_artifact("a1") is None
    assert db.list_artifact_comments("a1") == [], "comments do not outlive it"


def test_comments_read_oldest_first(db):
    db.add_artifact({"id": "a1", "project_id": "p1", "kind": "plan", "title": "p"})
    db.add_artifact_comment("a1", "user", "first")
    db.add_artifact_comment("a1", "reviewer", "second")

    thread = db.list_artifact_comments("a1")
    assert [c["body"] for c in thread] == ["first", "second"]
    assert thread[1]["author"] == "reviewer"


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------

def test_record_needs_a_project(db):
    assert artifacts.record("plan", "no project", project_id="", db=db) is None
    assert db.list_artifacts("p1") == []


def test_record_rejects_an_unknown_kind(db):
    with pytest.raises(ValueError):
        artifacts.record("wallpaper", "nope", project_id="p1", db=db)


def test_record_without_a_database_is_best_effort_not_a_crash(monkeypatch):
    monkeypatch.setattr(artifacts, "_db", lambda: None)
    assert artifacts.record("plan", "orphan", project_id="p1") is None
    assert artifacts.list_for_project("p1") == []
    assert artifacts.get("a1") is None
    assert artifacts.comments("a1") == []


def test_a_body_is_capped_not_refused(db):
    huge = "x" * (artifacts.MAX_BODY + 5000)
    made = artifacts.record("plan", "long", project_id="p1", body=huge, db=db)
    assert made is not None
    assert len(db.get_artifact(made.id)["body"]) == artifacts.MAX_BODY


def test_the_producers_say_what_they_are(db):
    plan = artifacts.record_plan("p1", "- step one\n- step two", round_no=2,
                                 session_id="s1", db=db)
    assert plan.kind == "plan" and plan.title == "Round 2 plan"
    assert plan.session_id == "s1" and plan.round_no == 2

    assert artifacts.record_plan("p1", "   ", db=db) is None, "an empty plan is not a thing"

    result = artifacts.record_summary("p1", "3 bugs fixed", round_no=2,
                                      approved=False, score=71, db=db)
    stored = db.get_artifact(result.id)
    assert stored["meta"] == {"approved": False, "score": 71}, "the verdict rides along"


def test_a_screenshot_artifact_points_at_the_file(db, tmp_path):
    shot = tmp_path / "shot-1.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    made = artifacts.record_screenshot("p1", shot, url="http://127.0.0.1:8000/x",
                                       db=db)

    stored = db.get_artifact(made.id)
    assert stored["kind"] == "screenshot"
    assert stored["path"] == str(shot)
    assert stored["meta"]["url"] == "http://127.0.0.1:8000/x"
    assert stored["meta"]["filename"] == "shot-1.png"


def test_a_comment_needs_a_body(db):
    db.add_artifact({"id": "a1", "project_id": "p1", "kind": "plan", "title": "p"})
    ok = artifacts.add_comment("a1", "is this the right order?", db=db)
    assert ok["body"] == "is this the right order?" and ok["author"] == "user"
    with pytest.raises(ValueError):
        artifacts.add_comment("a1", "   ", db=db)


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

class FakeProject:
    id = "p1"


class FakeOrchestrator:
    def __init__(self, db):
        self._db = db

    def get_project(self, project_id):
        return FakeProject() if project_id == "p1" else None


@pytest.fixture
def client(db):
    from api.app import app

    real = _api_deps.orchestrator
    _api_deps.orchestrator = FakeOrchestrator(db)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        _api_deps.orchestrator = real


def test_the_api_lists_and_reads_and_comments(client, db):
    plan = artifacts.record_plan("p1", "- step one", round_no=1, db=db)

    listed = client.get("/api/projects/p1/artifacts").json()
    assert listed["count"] == 1 and listed["artifacts"][0]["id"] == plan.id

    one = client.get("/api/artifacts/" + plan.id).json()
    assert one["title"] == "Round 1 plan"

    posted = client.post("/api/artifacts/" + plan.id + "/comments",
                         json={"body": "why this order?"})
    assert posted.status_code == 200, posted.text
    assert posted.json()["body"] == "why this order?"

    thread = client.get("/api/artifacts/" + plan.id + "/comments").json()
    assert thread["count"] == 1


def test_the_api_filters_by_kind(client, db):
    artifacts.record_plan("p1", "- a", round_no=1, db=db)
    artifacts.record_report("p1", "Gate report", "# ok", db=db)

    plans = client.get("/api/projects/p1/artifacts?kind=plan").json()
    assert plans["count"] == 1 and plans["artifacts"][0]["kind"] == "plan"


def test_the_api_404s_rather_than_inventing(client):
    assert client.get("/api/artifacts/nope").status_code == 404
    assert client.get("/api/artifacts/nope/comments").status_code == 404
    assert client.post("/api/artifacts/nope/comments",
                       json={"body": "hi"}).status_code == 404
    assert client.post("/api/artifacts/nope/comments",
                       json={"body": ""}).status_code == 404, "404 first: it does not exist"
