"""Tests for the self-learning / reflection layer."""
from __future__ import annotations

import json

import pytest

from kairos.core.persistence import Persistence
from kairos.learning.reflect import (
    _extract_json,
    maybe_run_reflection,
)


@pytest.fixture
def db(tmp_path):
    return Persistence(tmp_path / "mem.db")


# ============================================================ _extract_json

def test_extract_json_plain():
    assert _extract_json('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}


def test_extract_json_fenced():
    s = "`json\n{\"a\": 1}\n`"
    assert _extract_json(s) == {"a": 1}


def test_extract_json_prose_wrapped():
    s = "Here is the result: {\"x\": \"y\"} -- done"
    assert _extract_json(s) == {"x": "y"}


def test_extract_json_invalid_returns_none():
    assert _extract_json("not json") is None
    assert _extract_json("") is None
    assert _extract_json(None) is None
    assert _extract_json("[1, 2, 3]") is None  # not a dict


# ============================================================ maybe_run_reflection

@pytest.mark.asyncio
async def test_maybe_run_reflection_empty_inputs(db):
    result = await maybe_run_reflection(db, "p1", [])
    assert result.get("skipped", 0) >= 1
    assert result.get("notes", 0) == 0


@pytest.mark.asyncio
async def test_maybe_run_reflection_heal_pattern_to_notes(db):
    """When a category recurs >=3 times, the heuristic pass writes a
    project note even without an LLM call."""
    rounds = []
    for i in range(4):
        rounds.append({
            "round": i + 1,
            "approve": 0,
            "score": 50,
            "coder_summary": "x",
            "review_summary": "r",
            "review_json": json.dumps({
                "issues": [{"category": "correctness", "severity": "MAJOR",
                            "description": f"missing null check round {i}"}],
            }),
        })
    result = await maybe_run_reflection(db, "p1", rounds)
    # The heuristic pass should have written at least one pitfall note.
    notes = db.list_project_notes("p1", limit=50)
    assert any(n["kind"] == "pitfall" for n in notes)
    assert result.get("notes", 0) >= 1


@pytest.mark.asyncio
async def test_maybe_run_reflection_respects_min_interval(db):
    """A second call within MIN_REFLECTION_INTERVAL_S must skip the
    cheap pass too so we don't hammer the DB."""
    rounds = [{"round": i + 1, "review_json": json.dumps({"issues": []})} for i in range(5)]
    # First call: passes the gate.
    await maybe_run_reflection(db, "p1", rounds)
    # Second call within the gate: should be skipped entirely.
    r2 = await maybe_run_reflection(db, "p1", rounds)
    assert r2.get("skipped", 0) >= 1


@pytest.mark.asyncio
async def test_maybe_run_reflection_swallows_exceptions(db):
    """If the persistence layer is misbehaving (raises), the reflection
    must not propagate. We force the issue by passing a non-persistence
    object that raises on every call."""
    class Boom:
        def __getattr__(self, name):
            def fail(*a, **kw):
                raise RuntimeError("boom")
            return fail
    rounds = [{"round": 1, "review_json": json.dumps({"issues": []})}]
    result = await maybe_run_reflection(Boom(), "p1", rounds)
    assert result is not None  # must not raise
