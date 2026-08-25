"""Tests for session resume and fork."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.sessions import (
    ResumedSession,
    SessionInfo,
    SessionManager,
    new_session_id,
)


# ---------------------------------------------------------------------------
# Stub Persistence
# ---------------------------------------------------------------------------


def _make_message(
    sender: str,
    topic: str,
    ts: float,
    session_id: str = "sess-abc",
    content: str = "x",
) -> dict:
    return {
        "sender": sender,
        "topic": topic,
        "timestamp": ts,
        "metadata": {"session_id": session_id, "project_id": "p1"},
        "content": content,
    }


def _make_round(
    session_id: str, ts: float, round_no: int = 1,
    approved: bool = True, score: int = 80,
) -> dict:
    return {
        "session_id": session_id,
        "ts": ts,
        "round": round_no,
        "approved": approved,
        "score": score,
        "summary": f"round {round_no}",
    }


def _make_db():
    """Build a stub Persistence that records what was written so
    tests can call back into the SessionManager."""
    db = MagicMock()
    # Stable data per call: the test controls what load_* returns.
    db._messages = []
    db._rounds = []
    db._checkpoints = []
    db._projects = []

    db.load_messages.side_effect = lambda limit, project_id: sorted(
        [m for m in db._messages
         if m.get("metadata", {}).get("project_id") == project_id],
        key=lambda m: m.get("timestamp") or 0.0,
    )[:limit]
    db.load_loop_rounds.side_effect = lambda project_id, limit: [
        r for r in db._rounds if r.get("project_id", project_id) == project_id
    ][:limit]
    db.load_checkpoints.side_effect = lambda project_id, limit: [
        c for c in db._checkpoints if c.get("project_id", project_id) == project_id
    ][:limit]
    db.load_projects.side_effect = lambda: list(db._projects)
    db.save_project.side_effect = lambda p: db._projects.append(p) or p
    return db


# ---------------------------------------------------------------------------
# new_session_id
# ---------------------------------------------------------------------------


def test_new_session_id_format():
    sid = new_session_id()
    assert sid.startswith("sess-")
    assert len(sid.split("-")[-1]) == 8


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_empty_project():
    db = _make_db()
    sm = SessionManager(db)
    assert sm.list_sessions("p1") == []


def test_list_sessions_groups_by_session_id():
    db = _make_db()
    db._messages = [
        _make_message("a", "x", 100, session_id="sess-1"),
        _make_message("b", "y", 110, session_id="sess-1"),
        _make_message("c", "z", 200, session_id="sess-2"),
    ]
    db._rounds = [
        _make_round("sess-1", 105, approved=True),
        _make_round("sess-2", 205, approved=False),
    ]
    sm = SessionManager(db)
    sessions = sm.list_sessions("p1")
    assert len(sessions) == 2
    by_id = {s.session_id: s for s in sessions}
    assert by_id["sess-1"].messages == 2
    assert by_id["sess-1"].rounds == 1
    assert by_id["sess-1"].approved_rounds == 1
    assert by_id["sess-2"].messages == 1
    assert by_id["sess-2"].approved_rounds == 0


def test_list_sessions_newest_first():
    db = _make_db()
    db._messages = [
        _make_message("a", "x", 100, session_id="sess-old"),
        _make_message("b", "y", 500, session_id="sess-new"),
    ]
    sm = SessionManager(db)
    sessions = sm.list_sessions("p1")
    assert [s.session_id for s in sessions] == ["sess-new", "sess-old"]


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_resume_returns_none_for_unknown_project():
    db = _make_db()
    sm = SessionManager(db)
    assert sm.resume("nope") is None


def test_resume_returns_latest_session_by_default():
    db = _make_db()
    db._projects = [{"id": "p1", "name": "demo"}]
    db._messages = [
        _make_message("a", "x", 100, session_id="sess-old", content="x"),
        _make_message("b", "y", 500, session_id="sess-new", content="y"),
        _make_message("c", "z", 510, session_id="sess-new", content="z"),
    ]
    sm = SessionManager(db)
    res = sm.resume("p1")
    assert res is not None
    # Latest by last_message_at, regardless of session_id.
    assert res.history[-1]["content"] == "z"


def test_resume_filters_by_session_id():
    db = _make_db()
    db._projects = [{"id": "p1", "name": "demo"}]
    db._messages = [
        _make_message("a", "x", 100, session_id="sess-1"),
        _make_message("b", "y", 200, session_id="sess-2"),
    ]
    sm = SessionManager(db)
    res = sm.resume("p1", session_id="sess-2")
    assert res is not None
    assert all(
        m["metadata"]["session_id"] == "sess-2" for m in res.history
    )


def test_resume_includes_loop_rounds_and_checkpoints():
    db = _make_db()
    db._projects = [{"id": "p1", "name": "demo"}]
    db._rounds = [
        {"session_id": "sess-1", "ts": 100, "round": 1, "approved": True, "score": 80, "summary": "ok", "project_id": "p1"},
    ]
    db._checkpoints = [
        {"session_id": "sess-1", "round": 1, "sha": "abc123", "score": 80,
         "approved": True, "summary": "ok", "ts": 100, "project_id": "p1"},
    ]
    sm = SessionManager(db)
    res = sm.resume("p1")
    assert res is not None
    assert len(res.loop_rounds) == 1
    assert len(res.checkpoints) == 1


# ---------------------------------------------------------------------------
# fork
# ---------------------------------------------------------------------------


def test_fork_unknown_source_returns_none():
    db = _make_db()
    sm = SessionManager(db)
    assert sm.fork("missing") is None


def test_fork_creates_new_project_record():
    db = _make_db()
    db._projects = [{
        "id": "src1", "name": "demo", "description": "old",
        "workspace": "/tmp/src", "work_dir": "/tmp/src",
        "requirements": "fix bug", "status": "completed",
        "created_at": 100.0,
    }]
    sm = SessionManager(db)
    new_rec = sm.fork("src1")
    assert new_rec is not None
    assert new_rec["id"] != "src1"
    assert len(new_rec["id"]) == 8
    # The new project should be saved (save_project got called with
    # the project instance).
    assert db.save_project.called
    # The last call to save_project was the new project.
    last_arg = db.save_project.call_args[0][0]
    name = getattr(last_arg, "name", None) or last_arg.get("name", "")
    assert name.endswith("(fork)")


def test_fork_with_custom_name():
    db = _make_db()
    db._projects = [{
        "id": "src1", "name": "demo", "description": "",
        "workspace": "/x", "work_dir": "/x", "requirements": "",
        "status": "active", "created_at": 0.0,
    }]
    sm = SessionManager(db)
    new_rec = sm.fork("src1", new_name="my-fork")
    assert new_rec["name"] == "my-fork"


def test_fork_with_custom_work_dir():
    db = _make_db()
    db._projects = [{
        "id": "src1", "name": "demo", "description": "",
        "workspace": "/x", "work_dir": "/x", "requirements": "",
        "status": "active", "created_at": 0.0,
    }]
    sm = SessionManager(db)
    new_rec = sm.fork("src1", work_dir="/new/work_dir")
    assert new_rec["work_dir"] == "/new/work_dir"
