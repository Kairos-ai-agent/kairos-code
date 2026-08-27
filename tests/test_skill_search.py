"""Tests for kairos.skill_search (Round 17 full-text skill search)."""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from kairos.skill_search import (
    build_index_from_loader,
    fts5_available,
    search,
    _search_python,
    _build_index,
    _skill_to_row,
)


class _FakeSkill:
    """Minimal stand-in for kairos.skills.Skill."""
    def __init__(self, name: str, body: str = "", priority: float = 0.5,
                 source_path: str = "/fake/skill.md"):
        self.name = name
        self.body = body
        self.priority = priority
        self.source_path = source_path


class _FakeLoader:
    def __init__(self, skills: List[_FakeSkill]):
        self._skills = skills
    def discover(self) -> List[_FakeSkill]:
        return self._skills


# ---------------------------------------------------------------------------
# fts5_available
# ---------------------------------------------------------------------------


def test_fts5_available_returns_bool():
    """The detection returns a boolean (no exception)."""
    assert isinstance(fts5_available(), bool)


# ---------------------------------------------------------------------------
# _skill_to_row
# ---------------------------------------------------------------------------


def test_skill_to_row_truncates_body():
    s = _FakeSkill("n", body="x" * 10_000)
    name, src, body, prio = _skill_to_row(s)
    assert name == "n"
    assert len(body) == 4096  # truncation cap


def test_skill_to_row_handles_missing_attrs():
    class _Bare:
        name = "x"
        # no body / priority / source_path
    name, src, body, prio = _skill_to_row(_Bare())
    assert name == "x"
    assert body == ""
    assert prio == 0.5  # default


# ---------------------------------------------------------------------------
# FTS5 index + search (when FTS5 is available)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not fts5_available(), reason="FTS5 not compiled in sqlite3")
def test_build_and_search_round_trip(tmp_path: Path):
    skills = [
        _FakeSkill("pytest-fixtures", body="use pytest fixtures for setup",
                   priority=0.8),
        _FakeSkill("react-hooks", body="React 18 hooks best practices",
                   priority=0.7),
        _FakeSkill("fastapi-routes", body="FastAPI REST endpoints",
                   priority=0.6),
    ]
    db = tmp_path / "idx.sqlite"
    n = build_index_from_loader(_FakeLoader(skills), db)
    assert n == 3
    assert db.exists()
    # Search for "pytest"
    results = search("pytest", db_path=db, limit=5)
    names = [r["name"] for r in results]
    assert "pytest-fixtures" in names
    # The pytest skill should rank above the others
    top = names[0]
    assert top == "pytest-fixtures"


@pytest.mark.skipif(not fts5_available(), reason="FTS5 not compiled in sqlite3")
def test_search_with_no_matches_returns_empty(tmp_path: Path):
    skills = [_FakeSkill("a", body="alpha")]
    db = tmp_path / "idx.sqlite"
    build_index_from_loader(_FakeLoader(skills), db)
    results = search("xyzzynothingmatches", db_path=db, limit=5)
    assert results == []


@pytest.mark.skipif(not fts5_available(), reason="FTS5 not compiled in sqlite3")
def test_search_respects_limit(tmp_path: Path):
    skills = [_FakeSkill(f"skill-{i}", body=f"common word {i}") for i in range(20)]
    db = tmp_path / "idx.sqlite"
    build_index_from_loader(_FakeLoader(skills), db)
    results = search("common", db_path=db, limit=5)
    assert len(results) == 5


# ---------------------------------------------------------------------------
# Python fallback
# ---------------------------------------------------------------------------


def test_python_search_finds_name_match():
    skills = [
        _FakeSkill("pytest-fixtures", body="random body"),
        _FakeSkill("react-hooks", body="pytest mentioned in body"),
    ]
    results = _search_python(skills, "pytest", limit=5)
    # The name match (pytest-fixtures) should outrank the body match
    assert results[0]["name"] == "pytest-fixtures"


def test_python_search_empty_query_returns_all_by_priority():
    skills = [
        _FakeSkill("a", priority=0.3),
        _FakeSkill("b", priority=0.9),
        _FakeSkill("c", priority=0.5),
    ]
    results = _search_python(skills, "", limit=5)
    names = [r["name"] for r in results]
    assert names == ["b", "c", "a"]


def test_python_search_no_match_returns_empty():
    skills = [_FakeSkill("a", body="hello")]
    results = _search_python(skills, "xyzzz", limit=5)
    assert results == []


def test_python_search_respects_limit():
    skills = [_FakeSkill(f"s{i}", body="common") for i in range(20)]
    results = _search_python(skills, "common", limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# search() with loader
# ---------------------------------------------------------------------------


def test_search_with_loader_uses_python_fallback(tmp_path: Path):
    """When no db_path is provided, search() uses the loader directly."""
    skills = [
        _FakeSkill("foo", body="bar baz"),
        _FakeSkill("qux", body="foo bar"),
    ]
    results = search("foo", loader=_FakeLoader(skills), limit=5)
    names = [r["name"] for r in results]
    # Both match (one by name, one by body); order is score-based
    assert "foo" in names
    assert "qux" in names


def test_search_with_loader_no_db_path_required():
    """search() can be called without a db_path (uses Python fallback)."""
    skills = [_FakeSkill("foo", body="bar")]
    results = search("foo", loader=_FakeLoader(skills), limit=5)
    assert isinstance(results, list)


def test_search_without_loader_or_db_raises():
    """A user error: no loader AND no db_path."""
    with pytest.raises(ValueError, match="Either db_path"):
        search("anything")


# ---------------------------------------------------------------------------
# Build-index edge cases
# ---------------------------------------------------------------------------


def test_build_index_replaces_existing_db(tmp_path: Path):
    db = tmp_path / "idx.sqlite"
    db.write_text("junk")  # pre-existing file
    skills = [_FakeSkill("a")]
    n = build_index_from_loader(_FakeLoader(skills), db)
    assert n == 1
    # The old file is gone
    assert db.exists()


def test_build_index_with_no_skills(tmp_path: Path):
    db = tmp_path / "idx.sqlite"
    n = build_index_from_loader(_FakeLoader([]), db)
    assert n == 0
    # The DB exists but is empty
    assert db.exists()
