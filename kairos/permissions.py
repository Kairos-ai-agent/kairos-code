"""Permission rules: allow / ask / deny for tool invocations.

Mirrors the agentic CLI's permission system. The user (or a project's
``permissions`` block) declares rules like::

    permissions:
      allow:
        - Bash(git diff:*)
        - Bash(git status)
        - Read(./src/**)
        - Edit(./tests/**)
      ask:
        - Bash(git push:*)
      deny:
        - Read(./.env)
        - Bash(rm -rf /*)

A rule's resource pattern is matched against the tool name + a
synthesized "resource" string. Wildcards work:

  - ``*`` matches any sequence of characters (anywhere)
  - Leading / trailing wildcards are equivalent
  - Patterns are evaluated in order; the first match wins
  - If no rule matches, the default decision is used (typically
    ``ask`` for safety)

The decision tree:

  1. ``deny`` rules  → always ``deny``
  2. ``ask`` rules   → always ``ask``
  3. ``allow`` rules → ``allow``
  4. default         → ``ask`` (configurable)

When the decision is ``ask``, the caller (UI / agent loop) is
expected to prompt the user. For headless modes (``kairos exec
--full-auto``) the default flips to ``allow``.
"""
from __future__ import annotations

import enum
import fnmatch
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


class Decision(str, enum.Enum):
    """Outcome of a permission check."""
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    def __str__(self) -> str:  # pragma: no cover - str() already works
        return self.value


# ---------------------------------------------------------------------------
# Rule representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionRule:
    """A single rule: ``Tool(resource_pattern)`` mapped to a decision.

    Patterns are compared against a synthesized resource string
    (e.g. for a Bash tool, the resource is the full command line).
    Wildcards:
      - ``*`` matches any sequence of characters (including ``/``)
      - ``**`` is the same as ``*`` (we don't distinguish — fnmatch
        semantics aren't useful for security rules)
    Matching is case-sensitive. The pattern is anchored at both
    ends — partial matches don't count.
    """
    tool: str
    pattern: str
    decision: Decision

    def _regex(self) -> re.Pattern:
        # Split on `*` first, then re.escape each chunk so the
        # `*` doesn't get escaped. The only wildcard we support is
        # `*` (and consecutive `*`s are equivalent to one). We don't
        # honor `?` or `[..]` because they add complexity without
        # much value for the "match a shell command" use case.
        parts = re.split(r"\*+", self.pattern)
        body = ".*".join(re.escape(p) for p in parts)
        return re.compile(f"^{body}$")

    def matches(self, tool: str, resource: str) -> bool:
        if self.tool != "*" and self.tool != tool:
            return False
        return self._regex().match(resource) is not None

    def to_dict(self) -> dict:
        return {"tool": self.tool, "pattern": self.pattern, "decision": self.decision.value}

    @classmethod
    def from_str(cls, raw: str, default_decision: Decision) -> "PermissionRule":
        """Parse ``Tool(pattern)`` shorthand, e.g. ``terminal(git diff*)``.

        If the string doesn't contain ``(`` we treat it as a bare
        tool name with ``*`` pattern (i.e. "any use of this tool").
        Whitespace inside the parentheses is preserved.

        The only wildcard is ``*`` and the match covers the whole resource, so
        ``terminal(git diff*)`` matches ``git diff --stat``. A colon is an
        ordinary character here — the ``git diff:*`` spelling copied from tools
        that use it compiles to a pattern demanding a literal colon, which no
        real command line has, so the rule silently never fires.
        """
        s = raw.strip()
        # Match `<tool>(<pattern>)` with optional whitespace
        # between the three pieces. The pattern is captured
        # non-greedily up to the LAST closing paren so patterns
        # can contain parens if they really need to.
        m = re.match(r"^\s*([\w\-]+)\s*\(\s*(.*?)\s*\)\s*$", s, re.DOTALL)
        if m:
            return cls(tool=m.group(1), pattern=m.group(2), decision=default_decision)
        # Bare tool name.
        return cls(tool=s, pattern="*", decision=default_decision)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass
class PermissionPolicy:
    """Evaluates permission requests against a list of rules."""
    rules: List[PermissionRule] = field(default_factory=list)
    default_decision: Decision = Decision.ASK

    def check(self, tool: str, resource: str) -> Tuple[Decision, Optional[PermissionRule]]:
        """Return (decision, matched_rule). First match wins.

        Order of evaluation: deny > ask > allow > default. Rules
        are not pre-sorted because the user's intended priority is
        whatever they wrote first; we respect that.
        """
        # First pass: any deny rule?
        for rule in self.rules:
            if rule.decision == Decision.DENY and rule.matches(tool, resource):
                return Decision.DENY, rule
        # Second pass: any ask rule?
        for rule in self.rules:
            if rule.decision == Decision.ASK and rule.matches(tool, resource):
                return Decision.ASK, rule
        # Third pass: any allow rule?
        for rule in self.rules:
            if rule.decision == Decision.ALLOW and rule.matches(tool, resource):
                return Decision.ALLOW, rule
        return self.default_decision, None

    def allows(self, tool: str, resource: str) -> bool:
        return self.check(tool, resource)[0] == Decision.ALLOW

    def denies(self, tool: str, resource: str) -> bool:
        return self.check(tool, resource)[0] == Decision.DENY

    def to_dict(self) -> dict:
        out: dict = {"default": self.default_decision.value, "rules": []}
        for r in self.rules:
            out["rules"].append(r.to_dict())
        return out


# ---------------------------------------------------------------------------
# Loading from YAML
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    if not path.exists() or not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except yaml.YAMLError as exc:
        logger.warning("permissions: bad YAML in %s: %s; using empty", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------------------
# Rules that cannot fire
# ---------------------------------------------------------------------------

def _certainly_covers(broad: "PermissionRule", narrow: "PermissionRule") -> bool:
    """True only when *broad* provably matches everything *narrow* can match.

    Deliberately conservative: the answer is used to tell the user a rule is
    dead, and "probably" is not good enough for that. Only the three shapes
    where the containment is exact are recognised.
    """
    if broad.tool not in ("*", narrow.tool):
        return False
    if broad.pattern == "*" or broad.pattern == narrow.pattern:
        return True
    # `git diff*` swallows `git diff--stat`; only a single trailing star.
    if broad.pattern.count("*") == 1 and broad.pattern.endswith("*"):
        return narrow.pattern.startswith(broad.pattern[:-1])
    return False


def detect_shadowed_rules(rules: List["PermissionRule"]) -> List[str]:
    """Allow rules the gate can never reach, each with the rule that eats it.

    ``PermissionPolicy.check`` consults every deny rule before any ask rule, and
    every ask rule before any allow rule, so one ``deny terminal(*)`` makes
    every narrower allow for that tool dead — including one added later to stop
    the prompts. A rule that silently does nothing is worse than no rule: the
    user believes the gate has been told.
    """
    findings: List[str] = []
    for rule in rules:
        if rule.decision is not Decision.ALLOW:
            continue
        label = f"{rule.tool}({rule.pattern})"
        if not rule.pattern.strip():
            findings.append(f"{label} can never match: the pattern is empty")
            continue
        for other in rules:
            if other is rule or other.decision is Decision.ALLOW:
                continue
            if not _certainly_covers(other, rule):
                continue
            findings.append(
                f"{label} never fires: {other.tool}({other.pattern}) "
                f"({other.decision.value}) is evaluated first")
            break
    return findings


def load_policy(
    project_dir: Optional[Path] = None,
    user_dir: Optional[Path] = None,
) -> PermissionPolicy:
    """Load and merge permission rules from project + user YAML.

    Resolution order: user → project. Within each file, the order
    of rules is preserved (the first matching rule wins, per
    the agentic CLI semantics).
    """
    user_dir = Path(user_dir) if user_dir else Path.home() / ".kairos"
    user_cfg = _load_yaml(user_dir / "permissions.yaml")
    proj_cfg = _load_yaml(Path(project_dir) / ".kairos" / "permissions.yaml") \
        if project_dir else {}

    rules: List[PermissionRule] = []
    # User first, then project (project can override / extend).
    for cfg in (user_cfg, proj_cfg):
        perms = cfg.get("permissions") or {}
        for decision_name, items in perms.items():
            try:
                decision = Decision(decision_name)
            except ValueError:
                logger.warning(
                    "permissions: unknown decision %r; skipping", decision_name
                )
                continue
            for item in items or []:
                rules.append(PermissionRule.from_str(str(item), decision))
    default = Decision.ASK
    for cfg in (user_cfg, proj_cfg):
        d = cfg.get("default_decision")
        if d:
            try:
                default = Decision(d)
            except ValueError:
                pass
    return PermissionPolicy(rules=rules, default_decision=default)


# ---------------------------------------------------------------------------
# Approval hook
# ---------------------------------------------------------------------------


def request_approval(policy: PermissionPolicy, tool: str, resource: str) -> Decision:
    """Convenience: ask the policy for a decision.

    In a real run, the caller (orchestrator / agent loop) will
    promote ``Decision.ASK`` into either ``ALLOW`` or ``DENY``
    based on user input. For headless modes, the caller can
    pre-apply an ``ApprovalMode`` (see ``approval.py``).
    """
    return policy.check(tool, resource)[0]


# Default templates for users to copy.
TEMPLATE_YAML = """\
# Kairos permission rules.
# A rule is one of:
#   allow: actions that don't need confirmation
#   ask:   actions that always need confirmation
#   deny:  actions that are always blocked
#
# Pattern syntax: Tool(pattern)
#   - tool is the BaseTool name (terminal, file_write, file_read, ...).
#     Use * to match any tool.
#   - pattern is a shell-style glob matched against the tool's
#     "resource" string (the file path, the command, the URL, etc.).
#   - * matches any sequence of characters.
#   - The first matching rule wins; order in this file = priority.
#
# Examples:
#   allow:
#     - file_read
#     - terminal(git status)
#     - file_edit(./src/**)
#     - terminal(git diff:*)
#   ask:
#     - terminal(git push:*)
#     - file_write(*.env)
#   deny:
#     - terminal(rm -rf /*)
#     - file_read(./secrets/**)

permissions:
  allow:
    - terminal(git status)
    - terminal(git diff:*)
    - terminal(git log:*)
    - file_read
    - file_edit(./src/**)
    - file_edit(./tests/**)
  ask:
    - file_write
    - terminal(git push:*)
    - terminal(npm install:*)
  deny:
    - terminal(rm -rf /*)
    - file_read(./.env)
    - file_read(./secrets/**)

# default_decision: ask   # one of allow | ask | deny
"""
