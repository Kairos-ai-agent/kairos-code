"""Tests for cross-loop memory persistence."""

import pytest

from kairos.core.persistence import Persistence

@pytest.fixture
def db(tmp_path):
    return Persistence(tmp_path / "mem.db")

def test_save_and_load_loop_round(db):
    review = {"approve": False, "score": 70,
              "summary": "two MAJOR issues found",
              "issues": [{"category": "correctness", "severity": "MAJOR",
                          "file": "x.py", "line": 10, "description": "bug",
                          "fix_instruction": "fix it"}]}
    db.save_loop_round("p1", "sess-1", 1,
                       coder_summary="wrote hello.py with a print",
                       review=review)

    rows = db.load_loop_rounds("p1")
    assert len(rows) == 1
    r = rows[0]
    assert r["round"] == 1
    assert r["coder_summary"] == "wrote hello.py with a print"
    assert r["score"] == 70
    assert r["approve"] == 0
    assert r["review_summary"] == "two MAJOR issues found"
    # Stored JSON must round-trip the issues list.
    import json
    parsed = json.loads(r["review_json"])
    assert parsed["issues"][0]["file"] == "x.py"

def test_load_loop_rounds_orders_chronologically(db):
    """Insertion-order = wall-clock order here (all in the same test).
    Loaded rounds should come back in the same order they were inserted."""
    for r in (3, 1, 2):
        db.save_loop_round("p1", "sess", r,
                           coder_summary=f"r{r}",
                           review={"approve": False, "score": 50, "summary": f"round {r}"})
    rows = db.load_loop_rounds("p1")
    rounds = [r["round"] for r in rows]
    # Order: inserted 3 first (oldest), then 1, then 2 (newest).
    # Result: oldest-first ordering.
    assert rounds == [3, 1, 2]

def test_load_loop_rounds_respects_limit(db):
    for r in range(10):
        db.save_loop_round("p1", "sess", r,
                           coder_summary=f"r{r}",
                           review={"approve": False, "score": 50, "summary": "x"})
    rows = db.load_loop_rounds("p1", limit=3)
    assert len(rows) == 3

def test_load_loop_rounds_isolates_projects(db):
    db.save_loop_round("p1", "s1", 1, "x", {"approve": True, "score": 90, "summary": "ok"})
    db.save_loop_round("p2", "s2", 1, "y", {"approve": False, "score": 30, "summary": "bad"})
    assert len(db.load_loop_rounds("p1")) == 1
    assert len(db.load_loop_rounds("p2")) == 1
    assert db.load_loop_rounds("p1")[0]["score"] == 90
    assert db.load_loop_rounds("p2")[0]["score"] == 30

def test_save_loop_round_is_idempotent(db):
    """Re-saving the same (project, session, round) overwrites the row
    rather than creating duplicates — important because the loop retries
    can call save_loop_round twice with the same key."""
    review_v1 = {"approve": False, "score": 30, "summary": "old",
                 "issues": [{"category": "x", "severity": "MAJOR",
                             "description": "d", "fix_instruction": "f"}]}
    review_v2 = {"approve": True, "score": 95, "summary": "new",
                 "issues": []}
    db.save_loop_round("p1", "s1", 1, "old", review_v1)
    db.save_loop_round("p1", "s1", 1, "new", review_v2)
    rows = db.load_loop_rounds("p1")
    assert len(rows) == 1
    assert rows[0]["score"] == 95
    assert rows[0]["coder_summary"] == "new"

def test_load_last_loop_summary(db):
    assert db.load_last_loop_summary("p1") is None  # nothing yet
    db.save_loop_round("p1", "s1", 1, "x",
                       {"approve": False, "score": 60, "summary": "first try failed"})
    db.save_loop_round("p1", "s1", 2, "y",
                       {"approve": True, "score": 92, "summary": "second try lgtm"})
    s = db.load_last_loop_summary("p1")
    assert "R2" in s
    assert "approved" in s
    assert "92" in s

def test_delete_loop_rounds_removes_only_that_project(db):
    db.save_loop_round("p1", "s1", 1, "x", {"approve": True, "score": 90, "summary": "ok"})
    db.save_loop_round("p2", "s2", 1, "y", {"approve": True, "score": 90, "summary": "ok"})
    db.delete_loop_rounds("p1")
    assert db.load_loop_rounds("p1") == []
    assert len(db.load_loop_rounds("p2")) == 1

# ---------------------------------------------------------------------- Loop digest builder

def test_history_digest_includes_past_rounds(db):
    """_load_history_digest must surface what prior rounds approved /
    rejected so the next loop picks up context."""
    from kairos.loop.review_loop import _load_history_digest
    db.save_loop_round("p1", "s1", 1, "coder1",
                       {"approve": False, "score": 40, "summary": "missing tests"})
    db.save_loop_round("p1", "s1", 2, "coder2",
                       {"approve": False, "score": 65, "summary": "tests added but flaky"})
    digest = _load_history_digest(db, "p1")
    assert "Previous loop history" in digest
    assert "R1 rejected" in digest
    assert "R2 rejected" in digest
    assert "missing tests" in digest
    assert "flaky" in digest

def test_history_digest_empty_for_new_project(db):
    from kairos.loop.review_loop import _load_history_digest
    assert _load_history_digest(db, "brand-new") == ""

def test_history_digest_no_persistence_returns_empty():
    """If orchestrator wasn't given a Persistence handle (e.g. in tests),
    digest is empty rather than crashing the loop."""
    from kairos.loop.review_loop import _load_history_digest
    assert _load_history_digest(None, "any") == ""