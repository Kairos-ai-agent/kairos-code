"""Structured plan tracking (TodoWrite-style) for the agent loop.

A "plan" is a list of todos. Each todo has:
  - ``status``: ``pending`` | ``in_progress`` | ``completed``
  - ``content``: imperative sentence ("Add CSV reader")
  - ``activeForm``: present-continuous ("Adding CSV reader")
                — what the agent is doing *right now* in the UI

The Coder agent emits a plan during plan mode (or whenever it
decides to), and updates it via the ``write_todos`` tool. The
plan rides along into the next round's system prompt and into
the WebSocket events so the UI can render a live checklist.

This is the simplest possible TodoWrite surface — taken from
``langchain-ai/deepagents`` and the agentic CLI's TodoWrite. We
deliberately don't try to be the planning tool that decides what
to do; the LLM does that. We're just the durable storage + render
layer.

Usage:
    from kairos.loop.plan import Plan, TodoItem, render_plan_block

    plan = Plan()
    plan.update([
        TodoItem(status="in_progress", content="Read README", activeForm="Reading README"),
        TodoItem(status="pending", content="Add CSV reader", activeForm="Adding CSV reader"),
    ])
    plan.mark_in_progress("Add CSV reader")
    plan.mark_completed("Add CSV reader")
    assert plan.completion == 1 / 2

    # Inject into the system prompt
    if plan.todos:
        prompt_section = render_plan_block(plan)
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_STATUSES = ("pending", "in_progress", "completed")


@dataclass
class TodoItem:
    status: str
    content: str
    activeForm: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"invalid todo status: {self.status!r} "
                f"(must be one of {VALID_STATUSES})"
            )

    def to_dict(self) -> Dict[str, str]:
        d = {"status": self.status, "content": self.content}
        if self.activeForm:
            d["activeForm"] = self.activeForm
        return d


@dataclass
class Plan:
    """A structured todo list maintained across rounds.

    Persisted in-memory on the session. We do NOT round-trip through
    the LLM — the LLM emits a fresh plan each turn, and the runner
    applies it via ``update()``. This keeps the source of truth
    in our code, not the model's interpretation.
    """
    todos: List[TodoItem] = field(default_factory=list)
    updated_at: float = 0.0

    # --- mutation ---------------------------------------------------------

    def replace(self, todos: List[TodoItem]) -> None:
        """Replace the entire plan. Used when the LLM emits a fresh
        list. Validates each entry.
        """
        for t in todos:
            if not isinstance(t, TodoItem):
                raise TypeError(f"expected TodoItem, got {type(t).__name__}")
        self.todos = list(todos)
        self._touch()

    def update(self, todos: List[TodoItem]) -> None:
        """Same as ``replace`` — kept as a method-name alias for
        clarity at the call site."""
        self.replace(todos)

    def mark_in_progress(self, content: str) -> bool:
        for t in self.todos:
            if t.content == content:
                t.status = "in_progress"
                self._touch()
                return True
        return False

    def mark_completed(self, content: str) -> bool:
        for t in self.todos:
            if t.content == content:
                t.status = "completed"
                self._touch()
                return True
        return False

    def _touch(self) -> None:
        import time
        self.updated_at = time.time()

    # --- queries ----------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return not self.todos

    @property
    def completion(self) -> float:
        """0.0 - 1.0 — fraction of todos that are completed."""
        if not self.todos:
            return 0.0
        return sum(1 for t in self.todos if t.status == "completed") / len(self.todos)

    @property
    def current(self) -> Optional[TodoItem]:
        """The single in-progress todo, if any (None if 0 or >1)."""
        in_prog = [t for t in self.todos if t.status == "in_progress"]
        if len(in_prog) == 1:
            return in_prog[0]
        return None

    # --- serialization ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "todos": [t.to_dict() for t in self.todos],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Plan":
        if not d:
            return cls()
        todos = [TodoItem(**t) for t in d.get("todos", [])]
        return cls(todos=todos, updated_at=float(d.get("updated_at", 0.0)))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_plan_block(plan: Plan) -> str:
    """Render the plan as a Markdown block for the system prompt.

    Returns an empty string if the plan is empty (caller can just
    skip the block). The block is intentionally compact — too
    long a plan eats context budget.
    """
    if plan.is_empty:
        return ""
    lines = ["# Plan", ""]
    for t in plan.todos:
        marker = {
            "completed": "x",
            "in_progress": ">",
            "pending": " ",
        }[t.status]
        active = f" — _{t.activeForm}_" if t.activeForm else ""
        lines.append(f"- [{marker}] {t.content}{active}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool call handler
# ---------------------------------------------------------------------------


def apply_write_todos(plan: Plan, tool_input: Dict[str, Any]) -> List[str]:
    """Apply a ``write_todos`` tool call from the LLM to the plan.

    The LLM's tool input shape:
        {"todos": [{"status": "...", "content": "...", "activeForm": "..."}]}

    Returns a list of human-readable diff lines (for the UI /
    history) so the user can see what changed.
    """
    raw = tool_input.get("todos", [])
    if not isinstance(raw, list):
        return [f"write_todos: invalid 'todos' (expected list, got {type(raw).__name__})"]
    new_items: List[TodoItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [f"write_todos: skipping non-dict entry: {entry!r}"]
        try:
            new_items.append(TodoItem(
                status=str(entry.get("status", "pending")),
                content=str(entry.get("content", "")),
                activeForm=str(entry.get("activeForm", "") or ""),
            ))
        except (ValueError, TypeError) as exc:
            return [f"write_todos: invalid entry {entry!r}: {exc}"]
    old_contents = {t.content for t in plan.todos}
    new_contents = {t.content for t in new_items}
    diffs: List[str] = []
    for c in sorted(new_contents - old_contents):
        diffs.append(f"+ {c}")
    for c in sorted(old_contents - new_contents):
        diffs.append(f"- {c}")
    for old_t, new_t in zip(plan.todos, new_items):
        if old_t.status != new_t.status:
            diffs.append(f"~ {old_t.content}: {old_t.status} → {new_t.status}")
    plan.replace(new_items)
    if not diffs:
        diffs.append("(no changes)")
    return diffs


# ---------------------------------------------------------------------------
# Round 14: plan history diffs (timeline view)
# ---------------------------------------------------------------------------


def plan_diff(before: Optional[Plan], after: Plan) -> List[Dict[str, str]]:
    """Compute a structured diff between two Plan snapshots.

    Returns a list of change records, one per todo, suitable for
    rendering as a timeline in the UI:
        {"op": "add",      "content": "X"}
        {"op": "remove",   "content": "Y"}
        {"op": "status",   "content": "Z", "from": "pending", "to": "completed"}
        {"op": "keep",     "content": "W", "status": "in_progress"}

    Either input may be ``None`` (treated as empty).
    """
    before_todos = before.todos if before is not None else []
    after_todos = after.todos
    before_by_content = {t.content: t for t in before_todos}
    after_by_content = {t.content: t for t in after_todos}
    diffs: List[Dict[str, str]] = []
    # Adds: content in after but not in before
    for content in after_by_content:
        if content not in before_by_content:
            t = after_by_content[content]
            diffs.append({"op": "add", "content": content,
                          "status": t.status})
    # Removes: content in before but not in after
    for content in before_by_content:
        if content not in after_by_content:
            t = before_by_content[content]
            diffs.append({"op": "remove", "content": content,
                          "status": t.status})
    # Status changes + keeps
    for content in after_by_content:
        if content in before_by_content:
            before_t = before_by_content[content]
            after_t = after_by_content[content]
            if before_t.status != after_t.status:
                diffs.append({
                    "op": "status",
                    "content": content,
                    "from": before_t.status,
                    "to": after_t.status,
                })
            else:
                diffs.append({
                    "op": "keep",
                    "content": content,
                    "status": after_t.status,
                })
    return diffs


def plan_history_from_session_history(
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build a per-round plan timeline from ``session.history``.

    Each input history entry may have a ``plan`` field (R12.2
    added this). This function returns one timeline record per
    round:
        {"round": int, "diff": list[dict], "completion": float,
         "todos": list[TodoItem]}

    The diff is computed against the previous round's plan. If
    no plan was captured for a round, the diff is empty (we
    can't reconstruct what the agent was doing).
    """
    timeline: List[Dict[str, Any]] = []
    prev_plan: Optional[Plan] = None
    for h in history:
        plan_dict = h.get("plan")
        if not plan_dict or not isinstance(plan_dict, dict):
            continue
        try:
            plan = Plan.from_dict(plan_dict)
        except Exception:
            continue
        if plan.is_empty:
            prev_plan = plan
            continue
        diffs = plan_diff(prev_plan, plan)
        timeline.append({
            "round": h.get("round", 0),
            "diff": diffs,
            "completion": plan.completion,
            "todos": [t.to_dict() for t in plan.todos],
        })
        prev_plan = plan
    return timeline
