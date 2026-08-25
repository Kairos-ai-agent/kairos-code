"""Tests for approval modes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.approval import (
    ApprovalMode,
    READ_ONLY_TOOLS,
    decide,
    decide_with_mode_name,
)
from kairos.permissions import Decision, PermissionPolicy, PermissionRule


# ---------------------------------------------------------------------------
# ApprovalMode.parse
# ---------------------------------------------------------------------------


def test_parse_known_modes():
    assert ApprovalMode.parse("suggest") == ApprovalMode.SUGGEST
    assert ApprovalMode.parse("EDIT") == ApprovalMode.EDIT
    assert ApprovalMode.parse("Full-Auto") == ApprovalMode.FULL_AUTO
    assert ApprovalMode.parse("yolo") == ApprovalMode.FULL_AUTO


def test_parse_unknown_falls_back_to_suggest():
    assert ApprovalMode.parse("garbage") == ApprovalMode.SUGGEST
    assert ApprovalMode.parse("") == ApprovalMode.SUGGEST


def test_describe_returns_human_string():
    text = ApprovalMode.FULL_AUTO.describe()
    assert "Full-auto" in text
    assert "denied" in text


# ---------------------------------------------------------------------------
# decide(): policy + mode interaction
# ---------------------------------------------------------------------------


def test_deny_always_wins_regardless_of_mode():
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "rm -rf /*", Decision.DENY),
    ])
    for mode in (ApprovalMode.SUGGEST, ApprovalMode.EDIT, ApprovalMode.FULL_AUTO):
        d, reason = decide(policy, "Bash", "rm -rf /", mode)
        assert d == Decision.DENY, f"mode={mode}"


def test_allow_always_wins_regardless_of_mode():
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "git status", Decision.ALLOW),
    ])
    for mode in (ApprovalMode.SUGGEST, ApprovalMode.EDIT, ApprovalMode.FULL_AUTO):
        d, _ = decide(policy, "Bash", "git status", mode)
        assert d == Decision.ALLOW, f"mode={mode}"


def test_suggest_mode_asks_for_writes():
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "*", Decision.ASK),
        PermissionRule("Read", "*", Decision.ASK),
    ])
    d, _ = decide(policy, "Bash", "rm file", ApprovalMode.SUGGEST)
    assert d == Decision.ASK
    d, _ = decide(policy, "Read", "x.txt", ApprovalMode.SUGGEST)
    assert d == Decision.ASK


def test_suggest_mode_still_asks_for_reads():
    """SUGGEST still asks for reads because the policy says ASK and
    SUGGEST doesn't auto-allow anything beyond what the policy
    allows."""
    policy = PermissionPolicy(rules=[
        PermissionRule("Read", "*", Decision.ASK),
    ])
    d, _ = decide(policy, "Read", "x.txt", ApprovalMode.SUGGEST)
    assert d == Decision.ASK


def test_edit_mode_auto_allows_read_only_tools():
    policy = PermissionPolicy(rules=[
        PermissionRule("file_read", "*", Decision.ASK),
    ])
    d, _ = decide(policy, "file_read", "x.txt", ApprovalMode.EDIT)
    assert d == Decision.ALLOW


def test_edit_mode_still_asks_for_writes():
    policy = PermissionPolicy(rules=[
        PermissionRule("terminal", "*", Decision.ASK),
        PermissionRule("file_write", "*", Decision.ASK),
    ])
    d, _ = decide(policy, "terminal", "rm file", ApprovalMode.EDIT)
    assert d == Decision.ASK
    d, _ = decide(policy, "file_write", "out.txt", ApprovalMode.EDIT)
    assert d == Decision.ASK


def test_full_auto_mode_allows_anything_not_denied():
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "*", Decision.ASK),
        PermissionRule("Bash", "rm -rf /*", Decision.DENY),
    ])
    d, _ = decide(policy, "Bash", "ls", ApprovalMode.FULL_AUTO)
    assert d == Decision.ALLOW
    d, _ = decide(policy, "Bash", "rm -rf /", ApprovalMode.FULL_AUTO)
    assert d == Decision.DENY


def test_decide_with_mode_name_helper():
    policy = PermissionPolicy()
    d, _ = decide_with_mode_name(policy, "Bash", "ls", "full-auto")
    assert d == Decision.ALLOW
    d, _ = decide_with_mode_name(policy, "Bash", "ls", "suggest")
    assert d == Decision.ASK


def test_read_only_tools_set_lists_expected_names():
    assert "file_read" in READ_ONLY_TOOLS
    assert "grep" in READ_ONLY_TOOLS
    assert "find" in READ_ONLY_TOOLS
    # Writes/commands are NOT in the set.
    assert "file_write" not in READ_ONLY_TOOLS
    assert "terminal" not in READ_ONLY_TOOLS
    assert "file_edit" not in READ_ONLY_TOOLS


def test_decide_returns_helpful_reason_for_deny():
    policy = PermissionPolicy(rules=[
        PermissionRule("Bash", "rm -rf /*", Decision.DENY),
    ])
    d, reason = decide(policy, "Bash", "rm -rf /", ApprovalMode.SUGGEST)
    assert d == Decision.DENY
    assert "rm -rf /*" in reason or "denied by policy" in reason
