"""Tests for kairos.loop.plan (TodoWrite-style plan tracking)."""
from __future__ import annotations

import pytest

from kairos.loop.plan import (
    Plan,
    TodoItem,
    apply_write_todos,
    render_plan_block,
)


# ---------------------------------------------------------------------------
# TodoItem
# ---------------------------------------------------------------------------


def test_todo_item_requires_valid_status():
    with pytest.raises(ValueError):
        TodoItem(status="invalid", content="x")
    # Valid statuses don't raise
    for s in ("pending", "in_progress", "completed"):
        TodoItem(status=s, content="x")


def test_todo_item_to_dict_omits_empty_active_form():
    t = TodoItem(status="pending", content="a", activeForm="")
    assert t.to_dict() == {"status": "pending", "content": "a"}
    t2 = TodoItem(status="pending", content="a", activeForm="Doing a")
    assert t2.to_dict() == {"status": "pending", "content": "a",
                             "activeForm": "Doing a"}


# ---------------------------------------------------------------------------
# Plan mutation + queries
# ---------------------------------------------------------------------------


def test_plan_starts_empty():
    p = Plan()
    assert p.is_empty
    assert p.completion == 0.0
    assert p.current is None


def test_plan_replace_validates_types():
    p = Plan()
    with pytest.raises(TypeError):
        p.replace([{"status": "pending", "content": "x"}])  # dict, not TodoItem
    # Real TodoItem works
    p.replace([TodoItem(status="pending", content="x")])
    assert len(p.todos) == 1


def test_plan_mark_in_progress():
    p = Plan()
    p.replace([
        TodoItem(status="pending", content="a"),
        TodoItem(status="pending", content="b"),
    ])
    assert p.mark_in_progress("a") is True
    assert p.mark_in_progress("a") is True  # idempotent
    assert p.mark_in_progress("nonexistent") is False
    assert p.current.content == "a"
    assert p.todos[0].status == "in_progress"


def test_plan_mark_completed_updates_completion():
    p = Plan()
    p.replace([
        TodoItem(status="pending", content="a"),
        TodoItem(status="pending", content="b"),
    ])
    p.mark_completed("a")
    assert p.completion == 0.5
    p.mark_completed("b")
    assert p.completion == 1.0


def test_plan_current_is_none_when_zero_or_multiple_in_progress():
    p = Plan()
    p.replace([TodoItem(status="pending", content="a")])
    assert p.current is None
    p.mark_in_progress("a")
    assert p.current.content == "a"
    p.replace([
        TodoItem(status="in_progress", content="a"),
        TodoItem(status="in_progress", content="b"),
    ])
    # Two in_progress → current is None (ambiguous)
    assert p.current is None


# ---------------------------------------------------------------------------
# apply_write_todos
# ---------------------------------------------------------------------------


def test_apply_write_todos_full_replace():
    p = Plan()
    p.replace([
        TodoItem(status="in_progress", content="old-a"),
        TodoItem(status="pending", content="old-b"),
    ])
    diffs = apply_write_todos(p, {
        "todos": [
            {"status": "pending", "content": "new-a", "activeForm": "Doing new-a"},
        ],
    })
    assert p.todos[0].content == "new-a"
    assert p.todos[0].activeForm == "Doing new-a"
    # Diff includes adds/removals
    assert any(d.startswith("+ ") for d in diffs)
    assert any(d.startswith("- ") for d in diffs)


def test_apply_write_todos_no_diff():
    p = Plan()
    p.replace([TodoItem(status="pending", content="a")])
    diffs = apply_write_todos(p, {
        "todos": [{"status": "pending", "content": "a"}],
    })
    assert diffs == ["(no changes)"]


def test_apply_write_todos_status_change():
    p = Plan()
    p.replace([TodoItem(status="pending", content="a")])
    diffs = apply_write_todos(p, {
        "todos": [{"status": "completed", "content": "a"}],
    })
    assert any("pending → completed" in d for d in diffs)


def test_apply_write_todos_invalid_input():
    p = Plan()
    # Not a list
    diffs = apply_write_todos(p, {"todos": "not a list"})
    assert "invalid" in diffs[0]
    # Invalid status
    diffs = apply_write_todos(p, {
        "todos": [{"status": "weird", "content": "x"}],
    })
    assert any("invalid" in d for d in diffs)
    # Plan should be unchanged
    assert p.is_empty


def test_apply_write_todos_handles_missing_active_form():
    p = Plan()
    diffs = apply_write_todos(p, {
        "todos": [{"status": "pending", "content": "a"}],
    })
    assert p.todos[0].activeForm == ""  # default


# ---------------------------------------------------------------------------
# render_plan_block
# ---------------------------------------------------------------------------


def test_render_plan_block_empty_returns_empty_string():
    assert render_plan_block(Plan()) == ""


def test_render_plan_block_renders_checkboxes():
    p = Plan()
    p.replace([
        TodoItem(status="completed", content="Read README", activeForm="Reading"),
        TodoItem(status="in_progress", content="Add feature", activeForm="Adding"),
        TodoItem(status="pending", content="Run tests"),
    ])
    out = render_plan_block(p)
    assert "# Plan" in out
    assert "[x] Read README" in out
    assert "_Reading_" in out
    assert "[>] Add feature" in out
    assert "[ ] Run tests"


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_plan_round_trip_via_dict():
    p = Plan()
    p.replace([
        TodoItem(status="completed", content="a", activeForm="Doing a"),
        TodoItem(status="pending", content="b"),
    ])
    d = p.to_dict()
    restored = Plan.from_dict(d)
    assert len(restored.todos) == 2
    assert restored.todos[0].status == "completed"
    assert restored.todos[0].content == "a"
    assert restored.todos[0].activeForm == "Doing a"
    assert restored.todos[1].status == "pending"


def test_plan_from_none_returns_empty():
    p = Plan.from_dict(None)
    assert p.is_empty
