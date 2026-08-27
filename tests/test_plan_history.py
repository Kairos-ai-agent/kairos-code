"""Tests for Round 14 plan history diffs and timeline."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from kairos.loop.plan import (
    Plan,
    TodoItem,
    plan_diff,
    plan_history_from_session_history,
)


# ---------------------------------------------------------------------------
# plan_diff
# ---------------------------------------------------------------------------


def test_plan_diff_empty_to_first():
    before = Plan()
    after = Plan()
    after.replace([TodoItem(status="pending", content="A")])
    diffs = plan_diff(before, after)
    assert any(d["op"] == "add" and d["content"] == "A" for d in diffs)


def test_plan_diff_add_remove_status():
    before = Plan()
    before.replace([
        TodoItem(status="completed", content="A"),
        TodoItem(status="pending", content="B"),
    ])
    after = Plan()
    after.replace([
        TodoItem(status="completed", content="A"),
        TodoItem(status="in_progress", content="C"),  # new
        # B is gone
    ])
    diffs = plan_diff(before, after)
    by_op = {}
    for d in diffs:
        by_op.setdefault(d["op"], []).append(d)
    assert any(d["content"] == "C" for d in by_op.get("add", []))
    assert any(d["content"] == "B" for d in by_op.get("remove", []))
    # A was kept (status unchanged)
    assert any(d["op"] == "keep" and d["content"] == "A" for d in diffs)


def test_plan_diff_status_change():
    before = Plan()
    before.replace([TodoItem(status="pending", content="A")])
    after = Plan()
    after.replace([TodoItem(status="completed", content="A")])
    diffs = plan_diff(before, after)
    status_changes = [d for d in diffs if d["op"] == "status"]
    assert len(status_changes) == 1
    sc = status_changes[0]
    assert sc["content"] == "A"
    assert sc["from"] == "pending"
    assert sc["to"] == "completed"


def test_plan_diff_none_before():
    """None is treated as empty plan (everything is added)."""
    after = Plan()
    after.replace([TodoItem(status="pending", content="X")])
    diffs = plan_diff(None, after)
    assert all(d["op"] == "add" for d in diffs)


# ---------------------------------------------------------------------------
# plan_history_from_session_history
# ---------------------------------------------------------------------------


def test_plan_history_empty():
    assert plan_history_from_session_history([]) == []


def test_plan_history_single_round():
    plan = Plan()
    plan.replace([TodoItem(status="pending", content="do x")])
    history = [{"round": 1, "plan": plan.to_dict()}]
    timeline = plan_history_from_session_history(history)
    assert len(timeline) == 1
    assert timeline[0]["round"] == 1
    assert timeline[0]["completion"] == 0.0
    # Single-add diff (empty → 1 todo)
    assert any(d["op"] == "add" for d in timeline[0]["diff"])


def test_plan_history_multi_round_diff():
    """Round 1: add A. Round 2: complete A, add B."""
    p1 = Plan()
    p1.replace([TodoItem(status="pending", content="A")])
    p2 = Plan()
    p2.replace([
        TodoItem(status="completed", content="A"),
        TodoItem(status="pending", content="B"),
    ])
    history = [
        {"round": 1, "plan": p1.to_dict()},
        {"round": 2, "plan": p2.to_dict()},
    ]
    timeline = plan_history_from_session_history(history)
    assert len(timeline) == 2
    # Round 1: A added
    assert timeline[0]["completion"] == 0.0
    # Round 2: A marked completed, B added
    diffs_2 = {d.get("content"): d for d in timeline[1]["diff"]}
    assert diffs_2["A"]["op"] == "status"
    assert diffs_2["A"]["from"] == "pending"
    assert diffs_2["A"]["to"] == "completed"
    assert diffs_2["B"]["op"] == "add"
    assert timeline[1]["completion"] == 0.5  # 1 of 2 done


def test_plan_history_skips_rounds_without_plan():
    """Rounds without a captured plan don't appear in the timeline."""
    history = [
        {"round": 1, "plan": None},
        {"round": 2},  # no plan field at all
        {"round": 3, "plan": {"todos": [{"status": "pending", "content": "X"}]}},
    ]
    timeline = plan_history_from_session_history(history)
    assert len(timeline) == 1
    assert timeline[0]["round"] == 3


def test_plan_history_skips_corrupt_plan_dict():
    """A corrupt plan dict is logged-and-skipped, not fatal."""
    history = [
        {"round": 1, "plan": {"todos": "not a list"}},
        {"round": 2, "plan": {"todos": [{"status": "pending", "content": "ok"}]}},
    ]
    timeline = plan_history_from_session_history(history)
    # The corrupt one is skipped; the good one appears
    assert len(timeline) == 1
    assert timeline[0]["round"] == 2


def test_plan_history_completion_progression():
    """completion goes up as todos are completed."""
    p1 = Plan()
    p1.replace([
        TodoItem(status="pending", content="A"),
        TodoItem(status="pending", content="B"),
    ])
    p2 = Plan()
    p2.replace([
        TodoItem(status="completed", content="A"),
        TodoItem(status="pending", content="B"),
    ])
    p3 = Plan()
    p3.replace([
        TodoItem(status="completed", content="A"),
        TodoItem(status="completed", content="B"),
    ])
    history = [
        {"round": 1, "plan": p1.to_dict()},
        {"round": 2, "plan": p2.to_dict()},
        {"round": 3, "plan": p3.to_dict()},
    ]
    timeline = plan_history_from_session_history(history)
    completions = [t["completion"] for t in timeline]
    assert completions == [0.0, 0.5, 1.0]
