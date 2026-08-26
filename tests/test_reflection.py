"""Tests for the Coder self-reflection module."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List

import pytest

from kairos.reflection import (
    Reflection,
    build_reflect_prompt,
    parse_reflection,
    run_reflection,
    save_reflection_to_memory,
)


# ---------------------------------------------------------------------------
# build_reflect_prompt
# ---------------------------------------------------------------------------


def test_prompt_contains_all_sections():
    p = build_reflect_prompt(
        requirement="add /metrics endpoint",
        outcome="approved",
        rounds=3,
        round_digests=[
            {"round": 1, "score": 50, "approve": False, "notes": "wip"},
            {"round": 2, "score": 80, "approve": True, "notes": "done"},
        ],
    )
    assert "WHAT_WENT_WELL" in p
    assert "WHAT_TO_IMPROVE" in p
    assert "NEXT_ACTIONS" in p
    assert "approved" in p
    assert "round 1" in p and "round 2" in p
    assert "/metrics endpoint" in p


def test_prompt_truncates_long_requirement():
    p = build_reflect_prompt(
        requirement="x" * 5000,
        outcome="cost_cap",
        rounds=10,
        round_digests=[],
    )
    assert len(p) < 5000
    assert "x" * 2000 in p


def test_prompt_handles_empty_digests():
    p = build_reflect_prompt("req", "no_progress", 5, [])
    assert "(no round digests recorded)" in p


# ---------------------------------------------------------------------------
# parse_reflection
# ---------------------------------------------------------------------------


def test_parse_full_three_sections():
    text = """Some preamble...

WHAT_WENT_WELL:
- Clear requirements before starting
- Wrote tests first
- Used a focused refactor

WHAT_TO_IMPROVE:
- Should have asked before adding deps
- Could batch small edits

NEXT_ACTIONS:
- Add an integration test for the metrics endpoint
"""
    r = parse_reflection(text)
    assert r.what_went_well == [
        "Clear requirements before starting",
        "Wrote tests first",
        "Used a focused refactor",
    ]
    assert r.what_to_improve == [
        "Should have asked before adding deps",
        "Could batch small edits",
    ]
    assert r.next_actions == [
        "Add an integration test for the metrics endpoint",
    ]
    assert r.raw == text


def test_parse_handles_missing_sections():
    text = """WHAT_WENT_WELL:
- Only this one
"""
    r = parse_reflection(text)
    assert r.what_went_well == ["Only this one"]
    assert r.what_to_improve == []
    assert r.next_actions == []


def test_parse_handles_no_headers():
    text = "just some random thoughts without headers"
    r = parse_reflection(text)
    assert r.raw == text
    assert r.what_went_well == []


def test_parse_handles_empty_string():
    r = parse_reflection("")
    assert r.raw == "" and r.what_went_well == []


def test_parse_strips_section_headers_inside_bullets():
    text = """WHAT_WENT_WELL:
- Good first
WHAT_TO_IMPROVE:
- Bad first
"""
    r = parse_reflection(text)
    assert r.what_went_well == ["Good first"]
    assert r.what_to_improve == ["Bad first"]


def test_parse_handles_dash_only_as_skip():
    text = """WHAT_WENT_WELL:
-
- Real bullet
NEXT_ACTIONS:
—
"""
    r = parse_reflection(text)
    assert r.what_went_well == ["Real bullet"]
    assert r.next_actions == []


def test_parse_case_insensitive_headers():
    text = """what_went_well:
- works in lowercase
WHAT_TO_IMPROVE:
- also good
"""
    r = parse_reflection(text)
    assert r.what_went_well == ["works in lowercase"]
    assert r.what_to_improve == ["also good"]


# ---------------------------------------------------------------------------
# Reflection serialization
# ---------------------------------------------------------------------------


def test_reflection_roundtrip_json():
    r = Reflection(
        what_went_well=["a", "b"],
        what_to_improve=["c"],
        next_actions=["d", "e"],
        rounds=3,
        outcome="approved",
        final_score=85.0,
        raw="full text",
    )
    j = r.to_json()
    parsed = Reflection.from_dict(json.loads(j))
    assert parsed.what_went_well == ["a", "b"]
    assert parsed.outcome == "approved"
    assert parsed.final_score == 85.0


# ---------------------------------------------------------------------------
# save_reflection_to_memory
# ---------------------------------------------------------------------------


def test_save_reflection_to_memory_no_layer(monkeypatch):
    """If the memory layer is unavailable, the call is a safe no-op."""
    import kairos.reflection as rmod
    monkeypatch.setattr(rmod, "_memory_layer", lambda: None)
    saved = save_reflection_to_memory("p1", Reflection(what_went_well=["a"]))
    assert saved is False


def test_save_reflection_to_memory_uses_record(monkeypatch):
    """If the memory layer has ``record``, we use it."""
    import kairos.reflection as rmod

    class FakeMemory:
        def __init__(self):
            self.saved = []

        def record(self, project_id, kind, body):
            self.saved.append((project_id, kind, body))

    fake = FakeMemory()
    monkeypatch.setattr(rmod, "_memory_layer", lambda: fake)
    saved = save_reflection_to_memory("p1", Reflection(what_went_well=["x"]))
    assert saved is True
    assert fake.saved == [("p1", "reflection", {"what_went_well": ["x"],
                                                "what_to_improve": [],
                                                "next_actions": [],
                                                "rounds": 0,
                                                "outcome": "",
                                                "final_score": 0.0,
                                                "raw": ""})]


def test_save_reflection_to_memory_uses_add_fallback(monkeypatch):
    import kairos.reflection as rmod

    class FakeMemory:
        def __init__(self):
            self.saved = []

        def add(self, project_id, kind, body):
            self.saved.append((project_id, kind, body))

    fake = FakeMemory()
    monkeypatch.setattr(rmod, "_memory_layer", lambda: fake)
    saved = save_reflection_to_memory("p2", Reflection(what_to_improve=["y"]))
    assert saved is True
    assert fake.saved[0][0] == "p2"


def test_save_reflection_to_memory_handles_exception(monkeypatch):
    import kairos.reflection as rmod

    class FakeMemory:
        def record(self, *a, **kw):
            raise RuntimeError("disk full")

    monkeypatch.setattr(rmod, "_memory_layer", lambda: FakeMemory())
    saved = save_reflection_to_memory("p3", Reflection())
    assert saved is False


# ---------------------------------------------------------------------------
# run_reflection (end-to-end with stub agent)
# ---------------------------------------------------------------------------


@dataclass
class StubCoder:
    response: str
    called_with: str = ""

    def generate(self, prompt):
        self.called_with = prompt
        return self.response

    async def agenerate(self, prompt):
        self.called_with = prompt
        return self.response


@pytest.mark.asyncio
async def test_run_reflection_sync_agent(monkeypatch):
    import kairos.reflection as rmod

    class FakeMemory:
        def record(self, **kw):
            pass
    monkeypatch.setattr(rmod, "_memory_layer", lambda: FakeMemory())

    coder = StubCoder(response="""WHAT_WENT_WELL:
- Got tests green

WHAT_TO_IMPROVE:
- Faster iteration

NEXT_ACTIONS:
- Add perf benchmark
""")
    r = await run_reflection(
        project_id="p1",
        coder_agent=coder,
        requirement="optimize x",
        outcome="approved",
        rounds=2,
        round_digests=[{"round": 1, "score": 80, "approve": True, "notes": "ok"}],
        final_score=85.0,
    )
    assert r.what_went_well == ["Got tests green"]
    assert r.what_to_improve == ["Faster iteration"]
    assert r.next_actions == ["Add perf benchmark"]
    assert r.outcome == "approved"
    assert r.rounds == 2
    assert r.final_score == 85.0
    assert "WHAT_WENT_WELL" in coder.called_with


@pytest.mark.asyncio
async def test_run_reflection_async_agent(monkeypatch):
    import kairos.reflection as rmod

    class FakeMemory:
        def record(self, **kw):
            pass
    monkeypatch.setattr(rmod, "_memory_layer", lambda: FakeMemory())

    coder = StubCoder(response="WHAT_WENT_WELL:\n- async path")
    r = await run_reflection(
        project_id="p2",
        coder_agent=coder,
        requirement="x",
        outcome="cost_cap",
        rounds=5,
        round_digests=[],
    )
    assert r.what_went_well == ["async path"]
    assert r.outcome == "cost_cap"


@pytest.mark.asyncio
async def test_run_reflection_handles_llm_failure(monkeypatch):
    import kairos.reflection as rmod
    monkeypatch.setattr(rmod, "_memory_layer", lambda: None)

    class Boom:
        def generate(self, prompt):
            raise RuntimeError("api down")

    r = await run_reflection(
        project_id="p3",
        coder_agent=Boom(),
        requirement="x",
        outcome="no_progress",
        rounds=3,
        round_digests=[],
    )
    # Empty reflection, but no exception
    assert r.what_went_well == []
    assert r.outcome == "no_progress"
    assert r.rounds == 3


@pytest.mark.asyncio
async def test_run_reflection_no_generate_method(monkeypatch):
    import kairos.reflection as rmod
    monkeypatch.setattr(rmod, "_memory_layer", lambda: None)

    class BadAgent:
        pass

    r = await run_reflection(
        project_id="p4",
        coder_agent=BadAgent(),
        requirement="x",
        outcome="approved",
        rounds=1,
        round_digests=[],
    )
    assert r.outcome == "approved"
    assert r.what_went_well == []
