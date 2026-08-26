"""Coder sub-modes.

Three modes are supported:

  - ``default``  — every tool the Coder normally has is available.
  - ``read_only`` — tools that can mutate state are removed. The Coder
    can still list / read / search / run git read-only commands, but
    cannot write files, run shell commands, edit code, or modify git.
    Useful for "review this PR" or "explain this codebase" tasks.
  - ``sandbox``  — write tools are allowed, but they are forced to
    operate inside a git worktree (the :mod:`kairos.worktree` module
    creates one and points the file/terminal tools at it). Destructive
    commands like ``rm -rf /`` are still blocked by the permissions
    layer. Useful for risky experiments the user wants to inspect
    before merging.

A :class:`ToolPolicy` is a small, testable object that takes a list of
tools and returns a filtered/rewritten list. The orchestrator wires
it in at agent construction time.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


class CoderMode(str, Enum):
    """Operating mode for the Coder agent."""
    DEFAULT = "default"
    READ_ONLY = "read_only"
    SANDBOX = "sandbox"

    @classmethod
    def parse(cls, value: Optional[str]) -> "CoderMode":
        """Parse a string (case-insensitive, forgiving). Falls back to DEFAULT."""
        if not value:
            return cls.DEFAULT
        s = str(value).strip().lower()
        for m in cls:
            if s == m.value or s == m.name.lower():
                return cls(m.value)
        # aliases
        if s in ("ro", "readonly", "read-only"):
            return cls.READ_ONLY
        if s in ("sb", "sandboxed", "worktree"):
            return cls.SANDBOX
        return cls.DEFAULT


# Tools that mutate state. Source of truth for read-only filtering.
# Anything matching one of these names (case-insensitive) gets removed
# from the tool list in READ_ONLY mode.
_MUTATING_TOOL_NAMES: Set[str] = {
    "file_edit", "file_write", "patch", "write_file",
    "terminal", "shell", "bash", "exec", "run_command",
    "git_commit", "git_push", "git_checkout", "git_reset",
    "create_checkpoint", "delete_checkpoint",
    "subagent", "spawn_agent",
    "checkpoint",
    "webfetch",  # considered side-effecting (network + can trigger actions)
    "create_agent", "delete_agent",
}

# Tools considered read-only — allowed in READ_ONLY mode.
_READ_ONLY_ALLOW: Set[str] = {
    "file_read", "read_file", "list_files", "list_directory",
    "grep", "find", "git_status", "git_log", "git_diff",
    "glob", "search", "stat",
}


@dataclass
class ToolPolicy:
    """Result of applying a Coder mode to a tool list.

    Attributes:
        mode: the CoderMode that was applied.
        allowed: tool names the agent may still call.
        blocked: tool names that were removed (and why).
        rewritten: tool name -> replacement name for tools that were
            transformed in place (currently only in SANDBOX mode).
    """

    mode: CoderMode
    allowed: List[str] = field(default_factory=list)
    blocked: List[Tuple[str, str]] = field(default_factory=list)  # (name, reason)
    rewritten: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "allowed": list(self.allowed),
            "blocked": [{"name": n, "reason": r} for n, r in self.blocked],
            "rewritten": dict(self.rewritten),
        }


def _is_mutating(name: str) -> bool:
    base = name.split(".")[-1].lower()  # handle "fs.write_file" -> "write_file"
    return base in _MUTATING_TOOL_NAMES


def _is_read_only(name: str) -> bool:
    base = name.split(".")[-1].lower()
    return base in _READ_ONLY_ALLOW


def apply_mode(
    tools: Sequence[Any],
    mode: CoderMode,
    *,
    sandbox_wrapper: Optional[Any] = None,
) -> Tuple[List[Any], ToolPolicy]:
    """Filter (and optionally rewrite) tools according to *mode*.

    Parameters
    ----------
    tools : sequence
        The agent's tool list. Each element must expose ``.name``;
        the tool itself (not just the name) is preserved in the result.
    mode : CoderMode
        Which policy to apply.
    sandbox_wrapper : optional callable
        Used only in ``sandbox`` mode. If given, each mutating tool is
        wrapped so that file/terminal operations are redirected to a
        worktree. Signature: ``wrapper(tool) -> wrapped_tool``. The
        default no-op wrapper is used when this is ``None``.

    Returns
    -------
    (filtered_tools, policy)
        ``filtered_tools`` is the new list to hand to the agent.
        ``policy`` records which names were removed/rewritten.
    """
    policy = ToolPolicy(mode=mode)

    if mode == CoderMode.DEFAULT:
        policy.allowed = [getattr(t, "name", "?") for t in tools]
        return list(tools), policy

    if mode == CoderMode.READ_ONLY:
        out: List[Any] = []
        for t in tools:
            name = getattr(t, "name", "")
            if _is_mutating(name):
                policy.blocked.append((name, "mutating tool blocked in read_only mode"))
                continue
            if not _is_read_only(name):
                # Unknown tool — be conservative and block it. This
                # prevents accidentally exposing e.g. a custom tool
                # that turns out to have side effects.
                policy.blocked.append((name, "not in read_only allow-list (default-deny)"))
                continue
            out.append(t)
        policy.allowed = [getattr(t, "name", "?") for t in out]
        return out, policy

    if mode == CoderMode.SANDBOX:
        out = []
        for t in tools:
            name = getattr(t, "name", "")
            if _is_mutating(name):
                wrapped = sandbox_wrapper(t) if sandbox_wrapper is not None else t
                wrapped_name = getattr(wrapped, "name", name)
                if wrapped_name != name:
                    policy.rewritten[name] = wrapped_name
                out.append(wrapped)
            else:
                out.append(t)
        policy.allowed = [getattr(t, "name", "?") for t in out]
        return out, policy

    # Should be unreachable.
    return list(tools), policy


# ---------------------------------------------------------------------------
# Helpers used by the orchestrator to wire the policy into agent creation
# ---------------------------------------------------------------------------


def mode_from_project_metadata(metadata: Optional[Dict[str, Any]]) -> CoderMode:
    """Read the Coder mode out of a project's metadata dict."""
    if not isinstance(metadata, dict):
        return CoderMode.DEFAULT
    val = metadata.get("coder_mode") or metadata.get("mode") or ""
    return CoderMode.parse(val)


# ---------------------------------------------------------------------------
# Prompt hints the agent sees in each mode
# ---------------------------------------------------------------------------


_MODE_HINTS: Dict[CoderMode, str] = {
    CoderMode.DEFAULT: "",
    CoderMode.READ_ONLY: (
        "\n\n[MODE: read-only] You cannot modify files or run mutating "
        "shell commands. Use list/read/search tools to answer the user's "
        "question. If they ask for a change, suggest the edit and ask for "
        "confirmation before re-running in default mode."
    ),
    CoderMode.SANDBOX: (
        "\n\n[MODE: sandbox] All writes happen inside a git worktree. "
        "Mutating tools are still available but their effect is isolated; "
        "the user reviews the diff before merging into the main branch."
    ),
}


def hint_for_mode(mode: CoderMode) -> str:
    return _MODE_HINTS.get(mode, "")
