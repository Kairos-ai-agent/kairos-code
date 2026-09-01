"""Approval modes: how much autonomy the agent has.

Mirrors the cloud task's three-mode ladder (Suggest / Edit / Full-Auto)
and the agentic CLI's `permissionMode` field. The mode is a single
value attached to the agent (or to a single `kairos exec` run);
it determines what happens when the permission policy says
"ask" (or the default).

  - ``SUGGEST``   : every non-trivial action (file write, shell
    command) requires the user to click OK. Read-only tools
    (file_read, grep) are silent.
  - ``EDIT``      : file writes/patches are silent. Shell commands
    still need a click.
  - ``FULL_AUTO`` : everything that isn't explicitly denied is
    silent. The agent can run for minutes without interrupting
    the user.

The mode is consulted by ``decide(policy, tool, resource)``,
which returns the final ``Decision.ALLOW``/``ASK``/``DENY`` for
a given tool call.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Tuple

from kairos.permissions import Decision, PermissionPolicy


class ApprovalMode(str, enum.Enum):
    """How much agent autonomy the user wants."""
    SUGGEST = "suggest"
    EDIT = "edit"
    FULL_AUTO = "full-auto"

    @classmethod
    def parse(cls, value: str) -> "ApprovalMode":
        """Tolerant parser: matches 'suggest', 'Suggest', 'EDIT',
        'edit', 'auto', 'full-auto' etc."""
        s = (value or "").strip().lower()
        if s in ("suggest", "default"):
            return cls.SUGGEST
        if s in ("edit", "auto-edit", "auto_edit"):
            return cls.EDIT
        if s in ("full-auto", "full_auto", "auto", "yolo"):
            return cls.FULL_AUTO
        # Unknown value: fall back to safest.
        return cls.SUGGEST

    def describe(self) -> str:
        return {
            ApprovalMode.SUGGEST:
                "Suggest: every file write and shell command needs your OK.",
            ApprovalMode.EDIT:
                "Edit: file writes are automatic; shell commands still need your OK.",
            ApprovalMode.FULL_AUTO:
                "Full-auto: nothing asks unless it's explicitly denied. Network disabled.",
        }[self]


# Tools considered "read-only" — silent in SUGGEST and EDIT modes.
READ_ONLY_TOOLS: frozenset = frozenset({
    "file_read", "grep", "find", "git_diff", "git_log", "git_show",
    "webfetch", "list_skills", "list_agents",
})


def decide(
    policy: PermissionPolicy,
    tool: str,
    resource: str,
    mode: ApprovalMode,
) -> Tuple[Decision, str]:
    """Resolve a tool call against the policy + approval mode.

    Returns ``(decision, reason)``. The reason string is human-
    readable and goes into the audit log so the user can see why
    the agent was allowed/asked/denied.
    """
    # Explicit deny in the policy always wins, regardless of mode.
    decision, matched = policy.check(tool, resource)
    if decision == Decision.DENY:
        return Decision.DENY, (
            f"denied by policy rule: {matched.tool}({matched.pattern})"
            if matched else "denied by policy"
        )
    if decision == Decision.ALLOW:
        return Decision.ALLOW, (
            f"allowed by policy rule: {matched.tool}({matched.pattern})"
            if matched else "allowed by policy"
        )
    # decision == Decision.ASK: the policy didn't have a
    # definitive allow/deny. Apply the approval mode.
    if mode == ApprovalMode.FULL_AUTO:
        return Decision.ALLOW, "full-auto mode allows anything not denied"
    if mode == ApprovalMode.EDIT and tool in READ_ONLY_TOOLS:
        return Decision.ALLOW, f"edit mode auto-allows read-only tool {tool!r}"
    return Decision.ASK, f"mode={mode.value} requires user approval"


def decide_with_mode_name(
    policy: PermissionPolicy,
    tool: str,
    resource: str,
    mode_name: str,
) -> Tuple[Decision, str]:
    """Convenience: parse the mode name, then decide."""
    return decide(policy, tool, resource, ApprovalMode.parse(mode_name))


# Default-mode helper for one-off CLI invocations.
DEFAULT_MODE = ApprovalMode.SUGGEST
