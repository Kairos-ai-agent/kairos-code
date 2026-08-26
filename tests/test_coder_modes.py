"""Tests for Coder sub-modes (default, read_only, sandbox)."""
from __future__ import annotations

import pytest

from kairos.coder_modes import (
    CoderMode,
    ToolPolicy,
    apply_mode,
    hint_for_mode,
    mode_from_project_metadata,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class FakeTool:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description or f"fake {name}"


def _default_toolset() -> list:
    return [
        FakeTool("file_read"),
        FakeTool("file_edit"),
        FakeTool("file_write"),
        FakeTool("terminal"),
        FakeTool("bash"),
        FakeTool("grep"),
        FakeTool("find"),
        FakeTool("git_status"),
        FakeTool("git_commit"),
        FakeTool("webfetch"),
        FakeTool("mcp.fs.read_file"),
        FakeTool("mcp.fs.write_file"),
    ]


# ---------------------------------------------------------------------------
# CoderMode.parse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("default", CoderMode.DEFAULT),
    ("DEFAULT", CoderMode.DEFAULT),
    ("", CoderMode.DEFAULT),
    (None, CoderMode.DEFAULT),
    ("read_only", CoderMode.READ_ONLY),
    ("read-only", CoderMode.READ_ONLY),
    ("ro", CoderMode.READ_ONLY),
    ("readonly", CoderMode.READ_ONLY),
    ("sandbox", CoderMode.SANDBOX),
    ("sb", CoderMode.SANDBOX),
    ("sandboxed", CoderMode.SANDBOX),
    ("worktree", CoderMode.SANDBOX),
    ("garbage", CoderMode.DEFAULT),  # unknown falls back to default
])
def test_parse_mode(raw, expected):
    assert CoderMode.parse(raw) == expected


def test_parse_mode_is_case_insensitive():
    assert CoderMode.parse("READ_ONLY") == CoderMode.READ_ONLY
    assert CoderMode.parse("Sandbox") == CoderMode.SANDBOX


# ---------------------------------------------------------------------------
# apply_mode — default
# ---------------------------------------------------------------------------


def test_default_mode_keeps_everything():
    tools = _default_toolset()
    out, policy = apply_mode(tools, CoderMode.DEFAULT)
    assert out == tools
    assert policy.mode == CoderMode.DEFAULT
    assert policy.blocked == []
    assert {t.name for t in out} == {t.name for t in tools}


# ---------------------------------------------------------------------------
# apply_mode — read_only
# ---------------------------------------------------------------------------


def test_read_only_strips_mutating_tools():
    tools = _default_toolset()
    out, policy = apply_mode(tools, CoderMode.READ_ONLY)
    names = {t.name for t in out}
    # read-only tools survive
    assert "file_read" in names
    assert "grep" in names
    assert "find" in names
    assert "git_status" in names
    # mutating tools removed
    assert "file_edit" not in names
    assert "file_write" not in names
    assert "terminal" not in names
    assert "bash" not in names
    assert "git_commit" not in names
    assert "webfetch" not in names
    # mcp.fs.write_file uses the suffix match
    assert "mcp.fs.write_file" not in names
    assert "mcp.fs.read_file" in names


def test_read_only_default_denies_unknown_tool():
    tools = [FakeTool("weird_unknown_tool")]
    out, policy = apply_mode(tools, CoderMode.READ_ONLY)
    assert out == []
    assert policy.blocked == [
        ("weird_unknown_tool", "not in read_only allow-list (default-deny)"),
    ]


def test_read_only_policy_records_blocked_with_reason():
    tools = _default_toolset()
    _, policy = apply_mode(tools, CoderMode.READ_ONLY)
    blocked_names = {n for n, _ in policy.blocked}
    assert "file_edit" in blocked_names
    assert "terminal" in blocked_names
    # each blocked entry has a non-empty reason
    for n, reason in policy.blocked:
        assert reason, f"empty reason for {n}"


# ---------------------------------------------------------------------------
# apply_mode — sandbox
# ---------------------------------------------------------------------------


def test_sandbox_keeps_all_tools_but_wraps_mutating():
    tools = _default_toolset()

    def wrap(t):
        # fake "worktree" wrapper: returns a wrapped tool whose name
        # is namespaced with the sandbox prefix.
        nt = FakeTool(f"worktree.{t.name}", description=t.description)
        return nt

    out, policy = apply_mode(tools, CoderMode.SANDBOX, sandbox_wrapper=wrap)
    names = {t.name for t in out}
    # read-only tools pass through unchanged
    assert "file_read" in names
    assert "grep" in names
    # mutating tools get rewritten
    assert "worktree.file_edit" in names
    assert "worktree.terminal" in names
    assert "worktree.bash" in names
    assert "worktree.git_commit" in names
    assert "worktree.mcp.fs.write_file" in names
    # original names are gone
    assert "file_edit" not in names
    assert "terminal" not in names
    # rewritten map is populated
    assert "file_edit" in policy.rewritten
    assert policy.rewritten["file_edit"] == "worktree.file_edit"


def test_sandbox_without_wrapper_keeps_original_names():
    """No sandbox_wrapper ⇒ mutating tools are kept with their original name.

    This still surfaces the policy intent via the mode and hint; it
    just doesn't physically rewrite. Callers that need a real wrapper
    (e.g. to redirect paths) pass one in.
    """
    tools = _default_toolset()
    out, policy = apply_mode(tools, CoderMode.SANDBOX)
    names = {t.name for t in out}
    assert names == {t.name for t in tools}
    assert policy.rewritten == {}


def test_sandbox_policy_allowed_lists_all_tools():
    tools = _default_toolset()
    out, policy = apply_mode(tools, CoderMode.SANDBOX)
    assert set(policy.allowed) == {t.name for t in out}
    assert policy.blocked == []


# ---------------------------------------------------------------------------
# ToolPolicy.to_dict
# ---------------------------------------------------------------------------


def test_policy_to_dict_shape():
    tools = _default_toolset()
    _, policy = apply_mode(tools, CoderMode.READ_ONLY)
    d = policy.to_dict()
    assert d["mode"] == "read_only"
    assert isinstance(d["allowed"], list)
    assert isinstance(d["blocked"], list)
    for b in d["blocked"]:
        assert "name" in b and "reason" in b
    assert d["rewritten"] == {}


# ---------------------------------------------------------------------------
# mode_from_project_metadata
# ---------------------------------------------------------------------------


def test_metadata_default_when_missing():
    assert mode_from_project_metadata(None) == CoderMode.DEFAULT
    assert mode_from_project_metadata({}) == CoderMode.DEFAULT


def test_metadata_reads_coder_mode():
    assert mode_from_project_metadata({"coder_mode": "read_only"}) == CoderMode.READ_ONLY
    assert mode_from_project_metadata({"mode": "sandbox"}) == CoderMode.SANDBOX


def test_metadata_ignores_non_dict():
    # Anything that's not a dict ⇒ default (no crash)
    assert mode_from_project_metadata("read_only") == CoderMode.DEFAULT
    assert mode_from_project_metadata(42) == CoderMode.DEFAULT


# ---------------------------------------------------------------------------
# hint_for_mode
# ---------------------------------------------------------------------------


def test_hint_default_is_empty():
    assert hint_for_mode(CoderMode.DEFAULT) == ""


def test_hint_read_only_warns_about_no_writes():
    h = hint_for_mode(CoderMode.READ_ONLY)
    assert "read-only" in h
    assert "modify" in h or "cannot" in h


def test_hint_sandbox_mentions_worktree():
    h = hint_for_mode(CoderMode.SANDBOX)
    assert "sandbox" in h
    assert "worktree" in h


# ---------------------------------------------------------------------------
# End-to-end: switch modes changes the same tool list
# ---------------------------------------------------------------------------


def test_same_toolset_three_modes_produce_three_distinct_policies():
    tools = _default_toolset()
    _, p_def = apply_mode(tools, CoderMode.DEFAULT)
    _, p_ro = apply_mode(tools, CoderMode.READ_ONLY)
    _, p_sb = apply_mode(tools, CoderMode.SANDBOX)
    assert p_def.allowed == [t.name for t in tools]
    assert len(p_ro.allowed) < len(p_def.allowed)  # read_only trims
    assert len(p_sb.allowed) == len(p_def.allowed)  # sandbox keeps all
    assert p_ro.blocked != []
    assert p_sb.blocked == []
