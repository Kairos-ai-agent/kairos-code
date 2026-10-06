"""WriteTodosTool — the schema the Coder needs in order to publish a plan.

The agent loop intercepts a ``write_todos`` call *before* dispatch and applies
it to the agent's plan tracker (``kairos.loop.plan.Plan``); the resulting
``plan.updated`` bus event is what drives the Loop page's "Current plan" panel
(``web/src/components/PlanPanel.tsx``) and the plan block in the next round's
system prompt. That interception is the real writer and it fires on the tool
*name* alone, before the permission gate — deliberately, because a plan update
is not an action on the outside world.

This tool exists so the model is *told* the tool exists. Without a schema in
the tool list no model ever emits the call, so the whole plan mechanism —
``kairos/loop/plan.py``, the ``plan.updated`` event, the UI panel — stayed
dark: the model could not call a tool it had never heard of.

``execute`` below is only a fallback for a context where no plan tracker is
reachable (a one-shot chat run rather than a loop run). There it validates the
payload the same way ``apply_write_todos`` does and, if a tracker was bound at
construction, applies the update; otherwise it acknowledges without pretending
that anything was persisted.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

#: The same enumeration ``kairos.loop.plan.TodoItem`` validates against.
TODO_STATUSES = ("pending", "in_progress", "completed")

_DESCRIPTION = (
    "Track progress on a multi-step task with a live todo list. Call this at "
    "the start of a round to record the plan, then call it again with the "
    "COMPLETE list whenever an item's status changes: mark the item you are "
    "working on in_progress and the ones you have finished completed. Each "
    "call REPLACES the previous list, so always resend every item — never just "
    "the one that changed. Keep items short and imperative (\"Add the CSV "
    "reader\"), with exactly one item in_progress at a time."
)


class WriteTodosTool(BaseTool):
    """Publish the agent's todo list so the UI can render a live checklist."""

    name = "write_todos"
    description = _DESCRIPTION

    #: Optional. Set it (to a ``kairos.loop.plan.Plan``) when something other
    #: than the loop's interception will dispatch this tool, so the update
    #: still lands. Left ``None`` in the common case: the interception in
    #: ``KairosAgent`` is what writes the plan, and this is its schema.
    plan_tracker: Optional[Any] = None

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": ("The complete todo list, in order. "
                                        "Replaces any previous list."),
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": ("Imperative description of "
                                                    "the task, e.g. \"Add CSV "
                                                    "reader\"."),
                                },
                                "status": {
                                    "type": "string",
                                    "enum": list(TODO_STATUSES),
                                    "description": ("One of pending | "
                                                    "in_progress | completed."),
                                },
                                "activeForm": {
                                    "type": "string",
                                    "description": ("Present-continuous form "
                                                    "shown while the item is "
                                                    "in_progress, e.g. \"Adding "
                                                    "CSV reader\". Optional."),
                                },
                            },
                            "required": ["content", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["todos"],
                "additionalProperties": False,
            },
        }

    async def execute(self, todos: Optional[List[Any]] = None,
                      **kwargs: Any) -> ToolResult:
        plan = self.plan_tracker
        if plan is None:
            # No tracker reachable here. Say so plainly rather than claim the
            # list was recorded somewhere it cannot be seen.
            count = len(todos) if isinstance(todos, list) else 0
            return ToolResult(
                success=True,
                output=(f"write_todos received ({count} item(s)). No plan "
                        f"tracker is attached in this context, so nothing was "
                        f"persisted (the plan panel is a loop-run feature)."),
                metadata={"persisted": False, "items": count},
            )
        from kairos.loop.plan import apply_write_todos, render_plan_block
        try:
            diffs = apply_write_todos(plan, {"todos": todos or []})
        except Exception as exc:  # noqa: BLE001 — malformed input, not a crash
            logger.debug("write_todos apply failed: %s", exc)
            return ToolResult(success=False, output="",
                              error=f"write_todos failed: {exc}")
        # ``apply_write_todos`` returns a single "invalid ..." line when the
        # input was rejected; surface that as a tool error, same as the
        # interception does.
        if diffs and len(diffs) == 1 and diffs[0].startswith("write_todos: invalid"):
            return ToolResult(success=False, output="", error=diffs[0])
        return ToolResult(
            success=True,
            output=("Plan updated. Current state:\n"
                    + (render_plan_block(plan) or "(empty plan)")),
            metadata={"persisted": True,
                      "todos": [t.to_dict() for t in plan.todos]},
        )
