"""Tests for the R38.6.6 Task tracker fix.

The right-hand Task tracker used to read ``session.history[-1]["plan"]``
and walk it as a list of step dicts. A plan snapshot is actually
``{"todos": [{"status", "content", "activeForm"}], "updated_at": ...}``
— a DICT — so ``enumerate()`` yielded the key strings, every entry
failed ``isinstance(step, dict)`` and the endpoint always returned an
empty list: "No tasks yet" for every long task, even a running one.
And because the loop session (plan included) only ever lives in memory,
a backend restart lost even that.

The fix has four layers, all covered here:

  1. the Coder's plan todos, with their own per-item status;
  2. loop rounds, when the Coder never decomposed the task;
  3. persisted loop events, replayed after a backend restart;
  4. the bare requirement, so a task is never invisible.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.routes.workbench import (
    _first_line, _plan_items_from_events, _plan_task_items,
    _round_items_from_events, _round_items_from_session, _todos_of,
    _verdict_from_content,
)


# ---------------------------------------------------------------------------
# 1. the plan snapshot shape (the root cause)
# ---------------------------------------------------------------------------


def test_todos_of_reads_a_plan_snapshot_dict():
    """A snapshot is a dict — the old code iterated its keys."""
    snap = {"todos": [{"content": "build api", "status": "completed"},
                      {"content": "write tests", "status": "in_progress"}],
            "updated_at": 1.0}
    todos = _todos_of(snap)
    assert [t["content"] for t in todos] == ["build api", "write tests"]
    # a Plan object exposes to_dict()
    plan_obj = SimpleNamespace(to_dict=lambda: snap)
    assert len(_todos_of(plan_obj)) == 2
    # a bare list still works, junk is ignored
    assert len(_todos_of([{"content": "x"}, "nope", 3])) == 1
    assert _todos_of(None) == []


def test_plan_items_carry_per_todo_status_and_ids():
    """completed → done, in_progress → in_progress, and stable ids."""
    session = SimpleNamespace(
        session_id="s1",
        round=1,
        history=[{"round": 1, "plan": {"todos": [
            {"content": "build api", "status": "in_progress",
             "activeForm": "building api"},
            {"content": "write tests", "status": "pending"},
        ]}}],
        plan_todos=None,
    )
    items = _plan_task_items(session, round_n=1, running=True,
                             last_approve=False)
    assert [(i.id, i.title, i.status) for i in items] == [
        ("plan-1", "build api", "in_progress"),
        ("plan-2", "write tests", "pending"),
    ]
    assert items[0].detail == "building api"
    assert all(i.source == "plan" for i in items)


def test_plan_items_merge_snapshots_last_write_wins():
    """A todo completed in a later round shows as done, and new todos
    appended later still appear (one checklist for the whole task)."""
    session = SimpleNamespace(
        session_id="s1",
        round=3,
        history=[
            {"round": 1, "plan": {"todos": [
                {"content": "build api", "status": "in_progress"}]}},
            {"round": 3, "plan": {"todos": [
                {"content": "build api", "status": "completed"},
                {"content": "polish ui", "status": "in_progress"}]}},
        ],
        plan_todos=None,
    )
    items = _plan_task_items(session, round_n=3, running=False,
                             last_approve=True)
    assert [(i.title, i.status) for i in items] == [
        ("build api", "done"), ("polish ui", "in_progress")]


def test_plan_items_mark_unfinished_work_failed_when_loop_died():
    """The loop ended without approval: an in_progress item never
    finished, so it must not look pending or done."""
    session = SimpleNamespace(
        session_id="s1", round=2, history=[], plan_todos=None,
    )
    session.plan_todos = SimpleNamespace(to_dict=lambda: {"todos": [
        {"content": "build api", "status": "in_progress"}]})
    items = _plan_task_items(session, round_n=2, running=False,
                             last_approve=False)
    assert items[0].status == "failed"


# ---------------------------------------------------------------------------
# 2. rounds, when the Coder never made a plan
# ---------------------------------------------------------------------------


def test_rounds_are_listed_when_the_coder_never_planned():
    session = SimpleNamespace(
        session_id="s1",
        round=2,
        history=[
            {"round": 1, "review": {"approve": False, "score": 18,
                                    "summary": "login still broken"}},
            {"round": 2, "review": {"approve": True, "score": 91,
                                    "summary": "all tests green"}},
        ],
    )
    items = _round_items_from_session(session, running=False)
    assert [(i.round, i.status) for i in items] == [
        (1, "rejected"), (2, "done")]
    assert items[0].title == "login still broken"
    assert "score 18" in (items[0].detail or "")
    assert "rejected" in (items[0].detail or "")


def test_current_round_is_in_progress_while_running():
    session = SimpleNamespace(
        session_id="s1", round=3,
        history=[{"round": 1, "review": {"approve": False, "score": 50}},
                 {"round": 2, "review": {"approve": False, "score": 60}}],
    )
    items = _round_items_from_session(session, running=True)
    assert [(i.round, i.status) for i in items] == [
        (1, "rejected"), (2, "rejected"), (3, "in_progress")]


def test_rollback_round_is_failed():
    session = SimpleNamespace(
        session_id="s1", round=2,
        history=[{"round": 1, "rollback": True, "reason": "score regressed"}],
    )
    items = _round_items_from_session(session, running=False)
    assert items[0].status == "failed"
    assert "score regressed" in (items[0].detail or "")


# ---------------------------------------------------------------------------
# 3. replay of persisted events after a backend restart
# ---------------------------------------------------------------------------


def _ev(topic, ts, sender="p1.coder", content="", metadata=None):
    return {"topic": topic, "timestamp": ts, "sender": sender,
            "content": content, "metadata": metadata or {}}


def test_events_replay_rounds_with_verdicts():
    events = [
        _ev("loop.started", 100, "p1.orchestrator", "do the thing",
            {"session_id": "s9", "project_id": "p1"}),
        _ev("loop.coder_started", 110, metadata={"session_id": "s9",
                                                 "round": 1}),
        _ev("agent.response", 120, "p1.coder", "Round 1: built the api"),
        _ev("task.result", 130, "p1.reviewer",
            '{"approve": false, "score": 41, "summary": "api is incomplete"}'),
        _ev("loop.coder_started", 200, metadata={"session_id": "s9",
                                                 "round": 2}),
        _ev("agent.response", 210, "p1.coder", "Round 2: fixed the api"),
        _ev("task.result", 220, "p1.reviewer",
            '{"approve": true, "score": 88, "summary": "api complete"}'),
    ]
    items, sid = _round_items_from_events(events, running=False)
    assert sid == "s9"
    assert [(i.round, i.status) for i in items] == [
        (1, "rejected"), (2, "done")]
    assert items[0].title == "api is incomplete"
    assert items[1].title == "api complete"


def test_events_replay_marks_the_last_round_running():
    events = [
        _ev("loop.coder_started", 110, metadata={"session_id": "s9",
                                                 "round": 1}),
        _ev("loop.coder_started", 200, metadata={"session_id": "s9",
                                                 "round": 2}),
        _ev("agent.response", 210, "p1.coder", "Round 2 in flight"),
    ]
    items, _ = _round_items_from_events(events, running=True)
    assert [(i.round, i.status) for i in items] == [
        (1, "failed"), (2, "in_progress")]


def test_events_replay_reports_errors():
    events = [
        _ev("loop.coder_started", 110, metadata={"session_id": "s9",
                                                 "round": 1}),
        _ev("task.error", 120, "p1.coder",
            "Error code: 402 - Insufficient Balance"),
    ]
    items, _ = _round_items_from_events(events, running=False)
    assert items[0].status == "failed"
    assert "Insufficient Balance" in (items[0].detail or "")


def test_truncated_verdict_json_is_recovered():
    """The DB caps content at 2000 chars, so verdicts are cut mid-JSON."""
    raw = '{"approve": false, "score": 73, "summary": "needs work", ' \
          '"issues": [{"severity": "high", "description": "unhandled'
    verdict = _verdict_from_content(raw)
    assert verdict == {"approve": False, "score": 73, "summary": "needs work"}
    # prose is not a verdict
    assert _verdict_from_content("Tool call limit reached after 20 turns.") is None
    # a complete verdict still parses
    full = '{"approve": true, "score": 90}'
    assert _verdict_from_content(full)["approve"] is True


def test_first_line_ignores_empty_values():
    assert _first_line(None) == ""
    assert _first_line("") == ""
    assert _first_line("  \n second") == "second"
    assert _first_line("## Heading\nbody") == "Heading"


# ---------------------------------------------------------------------------
# 4. the endpoint: always something to show
# ---------------------------------------------------------------------------


def _client(monkeypatch, project):
    from api.app import app
    from api.routes import workbench as wb
    orch = MagicMock()
    orch.get_project = MagicMock(return_value=project)
    monkeypatch.setattr(wb, "_orch", lambda: orch)
    return TestClient(app), orch


def _project(session=None, requirements="", running=False):
    project = MagicMock()
    project.loop_session = session
    project.requirements = requirements
    if running:
        project.loop_task = MagicMock()
        project.loop_task.done = MagicMock(return_value=False)
    else:
        project.loop_task = None
    return project


def test_endpoint_returns_plan_items(monkeypatch):
    session = SimpleNamespace(
        session_id="s1", round=1, plan_todos=None,
        original_requirement="build a stock scanner",
        history=[{"round": 1, "plan": {"todos": [
            {"content": "build api", "status": "completed"},
            {"content": "wire ui", "status": "in_progress"}]}}],
    )
    c, _ = _client(monkeypatch, _project(session, running=True))
    data = c.get("/api/workbench/tasks?project_id=p1").json()
    assert data["source"] == "plan"
    assert data["task_title"] == "build a stock scanner"
    assert [(t["title"], t["status"]) for t in data["tasks"]] == [
        ("build api", "done"), ("wire ui", "in_progress")]


def test_endpoint_shows_the_bare_task_before_any_round(monkeypatch):
    """A task that just started has no plan and no round yet — the panel
    must still show it instead of "No tasks yet"."""
    session = SimpleNamespace(session_id="s1", round=1, history=[],
                              plan_todos=None,
                              original_requirement="build a stock scanner")
    c, _ = _client(monkeypatch, _project(session, running=True))
    data = c.get("/api/workbench/tasks?project_id=p1").json()
    assert data["source"] == "task"
    assert data["running"] is True
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["status"] == "in_progress"
    assert data["tasks"][0]["title"] == "build a stock scanner"


def test_endpoint_replays_events_after_a_restart(monkeypatch):
    """session is None after a restart — the messages table still has
    the rounds, so the tracker keeps working."""
    events = [
        _ev("loop.coder_started", 110, metadata={"session_id": "s9",
                                                 "round": 1}),
        _ev("task.result", 130, "p1.reviewer",
            '{"approve": true, "score": 90, "summary": "done"}'),
    ]
    c, orch = _client(monkeypatch, _project(None,
                                            requirements="build a scanner"))
    orch._db.load_events = MagicMock(return_value=events)
    data = c.get("/api/workbench/tasks?project_id=p1").json()
    assert data["source"] == "rounds"
    assert data["session_id"] == "s9"
    assert [(t["round"], t["status"]) for t in data["tasks"]] == [(1, "done")]


def test_endpoint_never_returns_an_empty_list_for_a_task(monkeypatch):
    """No session, no events — fall back to the requirement."""
    c, orch = _client(monkeypatch, _project(None, requirements="do stuff"))
    orch._db.load_events = MagicMock(return_value=[])
    data = c.get("/api/workbench/tasks?project_id=p1").json()
    assert data["source"] == "task"
    assert [t["title"] for t in data["tasks"]] == ["do stuff"]


def test_endpoint_404s_for_unknown_project(monkeypatch):
    from api.app import app
    from api.routes import workbench as wb
    orch = MagicMock()
    orch.get_project = MagicMock(return_value=None)
    monkeypatch.setattr(wb, "_orch", lambda: orch)
    r = TestClient(app).get("/api/workbench/tasks?project_id=nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# R38.6.6b: a decomposed plan survives a backend restart
# ---------------------------------------------------------------------------


def test_persisted_plan_is_preferred_over_rounds():
    events = [
        {"topic": "loop.coder_started", "timestamp": 1.0,
         "metadata": {"round": 1, "session_id": "s1"},
         "content": "Coder round 1 starting..."},
        {"topic": "plan.updated", "timestamp": 2.0, "content": "diff",
         "metadata": {"plan": {"todos": [
             {"content": "write api", "status": "completed",
              "activeForm": "writing api"},
             {"content": "wire ui", "status": "in_progress",
              "activeForm": "wiring ui"},
         ]}}},
    ]
    items = _plan_items_from_events(events, running=False)
    assert [t.title for t in items] == ["write api", "wire ui"]
    assert items[0].status == "done"
    assert items[1].status == "failed"  # loop is gone, item never finished
    assert "stopped" in (items[1].detail or "")
    assert items[0].source == "plan"


def test_persisted_plan_keeps_running_item_in_progress():
    events = [{"topic": "plan.updated", "timestamp": 1.0,
               "metadata": {"plan": {"todos": [
                   {"content": "run me", "status": "in_progress"}]}}}]
    items = _plan_items_from_events(events, running=True)
    assert items[0].status == "in_progress"


def test_endpoint_replays_a_persisted_plan_after_restart(monkeypatch):
    events = [
        {"topic": "loop.coder_started", "timestamp": 1.0,
         "metadata": {"round": 1, "session_id": "s1"}, "content": "x"},
        {"topic": "plan.updated", "timestamp": 2.0, "content": "d",
         "metadata": {"plan": {"todos": [
             {"content": "alpha", "status": "completed"},
             {"content": "beta", "status": "pending"}]}}},
    ]
    c, orch = _client(monkeypatch, _project(None))
    orch._db.load_events = MagicMock(return_value=events)
    data = c.get("/api/workbench/tasks?project_id=p1").json()
    assert data["source"] == "plan"
    assert [(t["title"], t["status"]) for t in data["tasks"]] == [
        ("alpha", "done"), ("beta", "pending")]
