"""Tests for permission rules."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.permissions import (
    Decision,
    PermissionPolicy,
    PermissionRule,
    TEMPLATE_YAML,
    load_policy,
    request_approval,
)


# ---------------------------------------------------------------------------
# Rule parsing
# ---------------------------------------------------------------------------


def test_rule_from_str_tool_with_pattern():
    rule = PermissionRule.from_str("Bash(git diff:*)", Decision.ALLOW)
    assert rule.tool == "Bash"
    assert rule.pattern == "git diff:*"
    assert rule.decision == Decision.ALLOW


def test_rule_from_str_bare_tool_name():
    rule = PermissionRule.from_str("file_read", Decision.ALLOW)
    assert rule.tool == "file_read"
    assert rule.pattern == "*"


def test_rule_from_str_strips_whitespace():
    rule = PermissionRule.from_str("  Bash ( git status )  ", Decision.ASK)
    assert rule.tool == "Bash"
    assert rule.pattern == "git status"


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


def test_rule_matches_exact_pattern():
    rule = PermissionRule("Bash", "git status", Decision.ALLOW)
    assert rule.matches("Bash", "git status") is True
    assert rule.matches("Bash", "git status --short") is False  # fnmatch exact
    assert rule.matches("Read", "git status") is False


def test_rule_supports_wildcards():
    rule = PermissionRule("Bash", "git diff *", Decision.ALLOW)
    assert rule.matches("Bash", "git diff HEAD")
    assert rule.matches("Bash", "git diff main..feature")
    assert not rule.matches("Bash", "git log --oneline")


def test_rule_with_tool_wildcard():
    rule = PermissionRule("*", "*.env", Decision.DENY)
    # *.env matches anything ending in .env — including a bare
    # `.env` (where the leading `*` matches an empty prefix).
    # This is fnmatch-style behavior: `*` is greedy and may match
    # nothing. If you want "must have a non-empty prefix", use
    # `*?.env` (we don't support `?`; close enough is a longer
    # pattern or a deny rule that lists exact paths).
    assert rule.matches("file_read", "test.env") is True
    assert rule.matches("file_read", "config/.env") is True
    assert rule.matches("file_read", "README") is False
    # A more specific rule: src/.env only.
    rule2 = PermissionRule("*", "src/.env", Decision.DENY)
    assert rule2.matches("file_read", "src/.env")
    assert not rule2.matches("file_read", "test/.env")


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------


def test_deny_beats_ask_beats_allow():
    """The order in the policy is honored, but deny > ask > allow
    in priority regardless of position."""
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "git *", Decision.ALLOW),
        PermissionRule("Bash", "git push *", Decision.ASK),
        PermissionRule("Bash", "git push --force *", Decision.DENY),
    ])
    # git push origin main: matches "git *" (allow) and "git push *"
    # (ask). ASK wins.
    d, _ = policy.check("Bash", "git push origin main")
    assert d == Decision.ASK
    # git push --force origin main matches all three; DENY wins.
    d, _ = policy.check("Bash", "git push --force origin main")
    assert d == Decision.DENY
    # git status only matches "git *"; ALLOW.
    d, _ = policy.check("Bash", "git status")
    assert d == Decision.ALLOW


def test_first_matching_rule_wins():
    """When two rules of the same decision both match, the first
    one in the list is the matched one.

    Note: when rules of *different* decisions both match, the
    order doesn't matter — deny > ask > allow. So a policy with
    [ALLOW `git *`, DENY `git status`] will deny `git status`
    because DENY trumps ALLOW.
    """
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "git status", Decision.ALLOW),
        PermissionRule("Bash", "git status", Decision.DENY),
    ])
    # Both match; DENY pass runs first → DENY.
    d, matched = policy.check("Bash", "git status")
    assert d == Decision.DENY
    # Identical-decision case: first match wins within a pass.
    policy2 = PermissionPolicy(rules=[
        PermissionRule("Bash", "git *", Decision.ALLOW),
        PermissionRule("Bash", "git status", Decision.ALLOW),
    ])
    d, matched = policy2.check("Bash", "git status")
    assert d == Decision.ALLOW
    assert matched.pattern == "git *"


def test_default_decision_used_when_nothing_matches():
    policy = PermissionPolicy(default_decision=Decision.ASK)
    assert policy.check("Read", "foo.txt")[0] == Decision.ASK
    policy2 = PermissionPolicy(default_decision=Decision.ALLOW)
    assert policy2.check("Read", "foo.txt")[0] == Decision.ALLOW


def test_deny_wins_even_if_later_allow_matches():
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "*", Decision.ALLOW),
        PermissionRule("Bash", "rm -rf /*", Decision.DENY),
    ])
    d, _ = policy.check("Bash", "rm -rf /")
    assert d == Decision.DENY


def test_allows_and_denies_helpers():
    policy = PermissionPolicy(rules=[
        PermissionRule("Read", "*", Decision.ALLOW),
        PermissionRule("Write", "*", Decision.DENY),
    ])
    assert policy.allows("Read", "x")
    assert policy.allows("Bash", "y") is False  # default ASK -> not allowed
    assert policy.denies("Write", "x")
    assert policy.denies("Read", "x") is False


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def test_load_policy_no_files_returns_empty():
    with __import__("tempfile").TemporaryDirectory() as d:
        policy = load_policy(project_dir=Path(d) / "missing",
                             user_dir=Path(d) / "missing")
    assert policy.rules == []
    assert policy.default_decision == Decision.ASK


def test_load_policy_from_yaml(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".kairos").mkdir()
    (proj / ".kairos" / "permissions.yaml").write_text(
        "permissions:\n"
        "  allow:\n"
        "    - terminal(git status)\n"
        "  deny:\n"
        "    - terminal(rm -rf /*)\n"
        "default_decision: ask\n",
        encoding="utf-8",
    )
    policy = load_policy(project_dir=proj, user_dir=tmp_path / "missing")
    assert len(policy.rules) == 2
    assert policy.allows("terminal", "git status")
    assert policy.denies("terminal", "rm -rf /")
    assert policy.check("terminal", "ls")[0] == Decision.ASK  # default


def test_load_policy_user_then_project_order():
    """User rules are loaded first, project rules appended; user's
    first matching rule still wins by position."""
    with __import__("tempfile").TemporaryDirectory() as d:
        user = Path(d) / "user"
        user.mkdir()
        proj = Path(d) / "proj"
        proj.mkdir()
        (proj / ".kairos").mkdir()
        (user / "permissions.yaml").write_text(
            "permissions:\n  allow:\n    - Read(*)\n",
            encoding="utf-8",
        )
        (proj / ".kairos" / "permissions.yaml").write_text(
            "permissions:\n  deny:\n    - Read(.env)\n",
            encoding="utf-8",
        )
        policy = load_policy(project_dir=proj, user_dir=user)
        assert policy.allows("Read", "src/main.py")
        # Project's deny rule takes precedence over user's allow.
        assert policy.denies("Read", ".env")


def test_template_yaml_has_examples():
    assert "allow:" in TEMPLATE_YAML
    assert "deny:" in TEMPLATE_YAML
    assert "terminal(git diff:*)" in TEMPLATE_YAML


# ---------------------------------------------------------------------------
# Integration: request_approval shortcut
# ---------------------------------------------------------------------------


def test_request_approval_returns_decision():
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "rm -rf /*", Decision.DENY),
    ])
    assert request_approval(policy, "Bash", "rm -rf /") == Decision.DENY
    assert request_approval(policy, "Bash", "ls") == Decision.ASK


def test_policy_to_dict_round_trip():
    policy = PermissionPolicy(rules=[
        PermissionRule("Read", "*", Decision.ALLOW),
    ], default_decision=Decision.DENY)
    d = policy.to_dict()
    assert d["default"] == "deny"
    assert d["rules"][0]["tool"] == "Read"
    assert d["rules"][0]["pattern"] == "*"
    assert d["rules"][0]["decision"] == "allow"
