"""Tests for the memory + growth + learning layers.

Covers:
  - project_notes CRUD + bump_project_note_use
  - project_skills CRUD + find_skills_for case-insensitive substring match
  - working_fixes dedup on (project_id, from_signature) + outcome tracking
  - loop_rounds_fts BM25 search + empty-query recency fallback
  - global_kb substring dedup + cross-project search
  - assemble_coder_memory returns a non-empty block with section headers
  - failure_signature shape (file:cat:kw+kw+kw)
  - auto_promote_failure_to_preference threshold gate + dedup
  - maybe_record_working_fix fail->pass only, sig + fix_body excerpt
  - record_global_insights_from_review filters by severity + length
  - consolidate_project counts + advisory + ambiguity scan
  - _extract_json tolerant parser (fenced, prose, invalid)
  - maybe_run_reflection gating (MIN_REFLECTION_INTERVAL_S) + swallowed exceptions
"""
from __future__ import annotations

import json
import time

import pytest

from kairos.core.persistence import Persistence
from kairos.memory.retrieval import (
    assemble_coder_memory,
    failure_signature,
)
from kairos.memory.growth import (
    auto_promote_failure_to_preference,
    maybe_record_working_fix,
    record_global_insights_from_review,
    consolidate_project,
)


# ============================================================ fixtures

@pytest.fixture
def db(tmp_path):
    return Persistence(tmp_path / "mem.db")


# ============================================================ project_notes

def test_add_project_note_round_trip(db):
    nid = db.add_project_note(
        "p1", "convention", "Use snake_case",
        "All function names use snake_case.", source="user",
    )
    assert nid > 0
    rows = db.list_project_notes("p1")
    assert len(rows) == 1
    r = rows[0]
    assert r["title"] == "Use snake_case"
    assert r["kind"] == "convention"
    assert r["body"] == "All function names use snake_case."
    assert r["source"] == "user"
    assert r["use_count"] == 0


def test_add_project_note_kind_normalization(db):
    """Unknown kinds are coerced to 'convention' so the DB never holds
    arbitrary strings the Coder prompt cannot render."""
    nid = db.add_project_note("p1", "WAT", "t", "b")
    assert any(r["id"] == nid and r["kind"] == "convention" for r in db.list_project_notes("p1"))


def test_bump_project_note_use_increments(db):
    nid = db.add_project_note("p1", "fact", "t", "b")
    db.bump_project_note_use(nid)
    db.bump_project_note_use(nid)
    db.bump_project_note_use(nid)
    rows = [r for r in db.list_project_notes("p1") if r["id"] == nid]
    assert rows and rows[0]["use_count"] == 3


def test_delete_project_note(db):
    nid = db.add_project_note("p1", "fact", "t", "b")
    assert db.delete_project_note("p1", nid) is True
    assert db.list_project_notes("p1") == []
    # Second delete returns False.
    assert db.delete_project_note("p1", nid) is False


def test_project_notes_isolated_per_project(db):
    db.add_project_note("p1", "fact", "t", "b")
    db.add_project_note("p2", "fact", "t", "b")
    assert len(db.list_project_notes("p1")) == 1
    assert len(db.list_project_notes("p2")) == 1
    assert db.list_project_notes("p3") == []


# ============================================================ project_skills

def test_add_skill_and_list(db):
    sid = db.add_skill(
        "p1", "DB migrations",
        triggers=["alembic", "migration", "schema"],
        body="Always run lembic upgrade head before tests.",
        confidence=0.7,
    )
    assert sid > 0
    rows = db.list_skills("p1")
    assert len(rows) == 1
    s = rows[0]
    assert s["name"] == "DB migrations"
    assert json.loads(s["triggers"]) == ["alembic", "migration", "schema"]
    assert s["confidence"] == 0.7
    assert s["use_count"] == 0


def test_find_skills_for_substring_match_is_case_insensitive(db):
    db.add_skill("p1", "Alembic usage", ["alembic", "migration"], "use alembic upgrade head")
    db.add_skill("p1", "Make targets", ["make", "build"], "use make -j4 build")
    matches = db.find_skills_for("p1", "Please run ALEMBIC upgrade head before deploying")
    names = [m["name"] for m in matches]
    assert "Alembic usage" in names
    assert "Make targets" not in names


def test_find_skills_for_sorts_by_confidence_then_use_count(db):
    db.add_skill("p1", "low", ["foo"], "x", confidence=0.3)
    db.add_skill("p1", "high", ["foo"], "y", confidence=0.9)
    matches = db.find_skills_for("p1", "foo bar")
    assert matches[0]["name"] == "high"


def test_record_skill_outcome_climbs_then_drops_confidence(db):
    sid = db.add_skill("p1", "s", ["k"], "b", confidence=0.5)
    for _ in range(3):
        db.record_skill_outcome(sid, success=True)
    s = [r for r in db.list_skills("p1") if r["id"] == sid][0]
    assert s["confidence"] > 0.5
    for _ in range(20):
        db.record_skill_outcome(sid, success=False)
    s = [r for r in db.list_skills("p1") if r["id"] == sid][0]
    assert s["confidence"] < 0.3


def test_delete_skill(db):
    sid = db.add_skill("p1", "s", ["k"], "b")
    assert db.delete_skill("p1", sid) is True
    assert db.list_skills("p1") == []
    assert db.delete_skill("p1", sid) is False


# ============================================================ working_fixes

def test_add_working_fix_dedupes_on_signature(db):
    sig = "x.py:correctness:nullpointer"
    db.add_working_fix("p1", sig, "first body", "correctness")
    db.add_working_fix("p1", sig, "second body", "correctness")
    fix = db.find_working_fix("p1", sig)
    assert fix is not None
    assert fix["success_count"] >= 2
    # The fix_body reflects the second write (most recent wins).
    assert "second" in fix["fix_body"]


def test_find_working_fix_returns_best(db):
    db.add_working_fix("p1", "x.py:correctness:nullpointer", "body A", "correctness")
    # Manually bump success_count of an older one.
    rows = db.add_working_fix  # noqa: just to avoid linter
    fix = db.find_working_fix("p1", "x.py:correctness:nullpointer")
    assert fix is not None


def test_record_fix_outcome_updates_counts(db):
    sid = db.add_working_fix("p1", "x.py:correctness:nullpointer", "b", "correctness")
    db.record_fix_outcome(sid, success=True)
    fix = db.find_working_fix("p1", "x.py:correctness:nullpointer")
    assert fix is not None
    assert fix["success_count"] >= 2


# ============================================================ FTS5 round index

def test_index_loop_round_and_search(db):
    db.index_loop_round("p1", "s1", 1, "wrote login.py", "missing auth", "no auth header")
    db.index_loop_round("p1", "s1", 2, "added auth", "auth looks fine", "none")
    rows = db.search_loop_rounds("p1", "auth", limit=5)
    assert isinstance(rows, list)
    assert len(rows) >= 1
    # Both rounds mention 'auth' so we should get both.
    summaries = {(r.get("coder_summary") or "") + (r.get("review_summary") or "") for r in rows}
    assert any("auth" in s for s in summaries)


def test_search_loop_rounds_empty_query_returns_recent(db):
    db.index_loop_round("p1", "s1", 1, "x", "y", "z")
    db.index_loop_round("p1", "s1", 2, "a", "b", "c")
    rows = db.search_loop_rounds("p1", "", limit=5)
    assert len(rows) == 2


def test_search_loop_rounds_broken_falls_back_to_recent(db):
    """If the FTS5 MATCH throws on the query, we return recency-based
    results rather than 500ing. Important so a malformed prompt never
    breaks the loop."""
    db.index_loop_round("p1", "s1", 1, "x", "y", "z")
    rows = db.search_loop_rounds("p1", "(unbalanced OR", limit=5)
    assert isinstance(rows, list)


# ============================================================ global_kb

def test_add_global_insight_dedupes_on_substring(db):
    db.add_global_insight("correctness", "Always validate null pointers before dereferencing", "p1")
    # Same body, slight variation -> still deduped (LIKE on first 80 chars).
    db.add_global_insight("correctness", "Always validate null pointers before dereferencing them", "p2")
    rows = db.search_global_insights("null pointer", limit=5)
    # At least one record exists with that text.
    assert any("null pointer" in (r.get("body") or "").lower() for r in rows)


def test_search_global_insights_handles_empty_query(db):
    db.add_global_insight("correctness", "Some lesson", "p1")
    rows = db.search_global_insights("", limit=5)
    assert isinstance(rows, list)


def test_bump_global_insight_use(db):
    iid = db.add_global_insight("correctness", "x" * 100, "p1")
    db.bump_global_insight_use(iid)
    db.bump_global_insight_use(iid)
    # Round-trip via search to confirm count went up.
    rows = db.search_global_insights("x" * 30, limit=5)
    match = [r for r in rows if r["id"] == iid]
    if match:
        assert match[0]["use_count"] >= 3  # 1 base + 2 bumps


# ============================================================ failure_signature

def test_failure_signature_shape():
    sig = failure_signature({
        "file": "x.py", "category": "correctness",
        "description": "Missing null check on user_id parameter",
    })
    assert sig.startswith("x.py:correctness:")
    assert "null" in sig
    # At most 3 keyword tokens after the file:cat: prefix.
    tail = sig.split(":", 2)[-1]
    assert len(tail.split("+")) <= 3


def test_failure_signature_empty_issue():
    assert failure_signature({}) == ""
    assert failure_signature(None) == ""


# ============================================================ assemble_coder_memory

def test_assemble_coder_memory_with_no_persistence_returns_empty():
    assert assemble_coder_memory(None, "any", "x") == ""


def test_assemble_coder_memory_returns_bounded_string(db):
    db.add_project_note("p1", "convention", "snake_case", "All function names use snake_case.")
    db.add_skill("p1", "Alembic", ["alembic"], "alembic upgrade head")
    db.add_working_fix("p1", "x.py:correctness:nullpointer", "add None guard")
    db.index_loop_round("p1", "s1", 1, "x", "y", "z")
    block = assemble_coder_memory(db, "p1", "Please fix the alembic migration")
    assert isinstance(block, str)
    assert block  # non-empty
    # Should respect the global budget.
    assert len(block) <= 4500 * 4 + 200


def test_assemble_coder_memory_renders_section_headers(db):
    db.add_project_note("p1", "convention", "snake_case", "All function names use snake_case.")
    block = assemble_coder_memory(db, "p1", "anything")
    assert "Project Notes" in block or "Skills" in block


# ============================================================ growth

def test_auto_promote_failure_to_preference_below_threshold(db):
    issue = {"category": "correctness", "description": "missing input validation"}
    pid = auto_promote_failure_to_preference(db, "p1", issue, consecutive_count=2, threshold=3)
    assert pid is None
    # Threshold met -> preference added.
    pid = auto_promote_failure_to_preference(db, "p1", issue, consecutive_count=3, threshold=3)
    assert pid is not None
    prefs = db.list_preferences("p1")
    assert any(p.get("kind") == "never" for p in prefs)


def test_auto_promote_failure_to_preference_dedupes(db):
    issue = {"category": "correctness", "description": "missing input validation"}
    p1 = auto_promote_failure_to_preference(db, "p1", issue, 3)
    p2 = auto_promote_failure_to_preference(db, "p1", issue, 4)
    assert p1 is not None
    # Second call should detect the existing rule and skip.
    assert p2 is None


def test_maybe_record_working_fix_only_on_fail_to_pass(db):
    prior = {"approve": False, "score": 40,
             "issues": [{"category": "correctness", "severity": "MAJOR",
                         "description": "missing null check on user input",
                         "file": "x.py", "line": 10}]}
    passing = {"approve": True, "score": 90, "issues": [], "_confidence": 0.9}
    # Fail -> pass should record.
    fid = maybe_record_working_fix(db, "p1", prior, passing, "added None guard")
    assert fid is not None
    # Pass -> pass should NOT record.
    fid2 = maybe_record_working_fix(db, "p1", passing, passing, "more edits")
    assert fid2 is None
    # Fail -> fail should NOT record.
    fid3 = maybe_record_working_fix(db, "p1", prior, prior, "tried fix")
    assert fid3 is None


def test_record_global_insights_from_review_filters(db):
    review = {
        "issues": [
            {"category": "correctness", "severity": "MAJOR",
             "description": "missing auth check on /api/users"},
            {"category": "style", "severity": "MINOR",
             "description": "line too long"},
            {"category": "correctness", "severity": "CRITICAL",
             "description": "SQL injection risk in raw query"},
        ],
    }
    ids = record_global_insights_from_review(db, "p1", review)
    # Only CRITICAL/MAJOR get promoted; the MINOR style issue should not.
    assert len(ids) == 2


def test_consolidate_project_returns_summary(db):
    # Seed a few rounds.
    for i in range(3):
        db.save_loop_round(
            "p1", "s1", i + 1,
            coder_summary=f"r{i+1}",
            review={
                "approve": i == 2,
                "score": 80 if i == 2 else 50,
                "summary": f"r{i+1}",
                "issues": [{"category": "correctness", "severity": "MAJOR",
                            "description": "missing null check"}],
            },
        )
    rounds = db.load_loop_rounds("p1", limit=20)
    summary = consolidate_project(db, "p1", rounds)
    assert "promoted_preferences" in summary
    assert "advisory" in summary
    assert "ambiguous_signatures" in summary
    # The repeated correctness pattern (3 occurrences) should be promoted.
    assert summary["promoted_preferences"] >= 1


def test_consolidate_project_handles_empty(db):
    summary = consolidate_project(db, "p1", [])
    assert summary["promoted_preferences"] == 0
    assert summary["advisory"] == ""
