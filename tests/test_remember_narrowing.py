"""``remember`` records the command, not the tool.

Approving a single ``git diff`` used to write ``terminal(*)`` — every shell
command for the rest of that project's life, no more questions. These tests
work on the mechanism itself: the pattern that gets written has to match the
command that was approved, and must not cover its neighbours.
"""
from __future__ import annotations

from kairos.permissions import Decision, PermissionRule
from kairos.sentinel import NEVER_REMEMBER_PROGRAMS, narrowest_pattern
from kairos.tools.terminal import TerminalTool


def _rule(pattern: str) -> PermissionRule:
    return PermissionRule.from_str(f"terminal({pattern})", Decision.ALLOW)


# --------------------------------------------------------------- narrowing

def test_a_subcommand_approval_narrows_to_the_subcommand():
    assert narrowest_pattern("terminal", "git diff --stat") == "git diff*"
    assert narrowest_pattern("terminal", "git log --oneline -5") == "git log*"
    assert narrowest_pattern("terminal", "npm run build") == "npm run*"


def test_flags_alone_do_not_widen_the_pattern():
    assert narrowest_pattern("terminal", "ls -la") == "ls*"
    assert narrowest_pattern("terminal", "pytest -q") == "pytest*"


def test_destructive_programs_are_never_remembered():
    for command in ("rm -rf build", "sudo apt install x", "dd if=/dev/zero",
                    "curl http://x", "shutdown /s", "taskkill /F /IM node.exe",
                    "chmod -R 777 .", "scp a host:b", "ssh host"):
        assert narrowest_pattern("terminal", command) is None, command


def test_a_path_prefixed_destroyer_is_still_recognised():
    """``C:\\Windows\\System32\\format.com`` must not slip past on spelling."""
    assert narrowest_pattern(
        "terminal", "C:\\Windows\\System32\\format.com C:") is None
    assert narrowest_pattern("terminal", "/sbin/mkfs.ext4 /dev/sda1") is None


def test_tools_that_are_not_shells_keep_the_whole_tool_grant():
    assert narrowest_pattern("write_file", "a/b.txt") == "*"
    assert narrowest_pattern("terminal", "   ") == "*"


# --------------------------------------------------------------- the rule it writes

def test_the_written_pattern_covers_the_approved_command():
    rule = _rule(narrowest_pattern("terminal", "git diff --stat"))
    assert rule.matches("terminal", "git diff --stat")
    assert rule.matches("terminal", "git diff HEAD~1")
    assert rule.matches("terminal", "git diff")  # bare form too


def test_the_written_pattern_does_not_cover_the_neighbours():
    """This is the regression: the old rule was ``*`` and covered everything."""
    rule = _rule(narrowest_pattern("terminal", "git diff --stat"))
    assert not rule.matches("terminal", "git push --force origin master")
    assert not rule.matches("terminal", "git status")
    assert not rule.matches("terminal", "rm -rf /")


def test_the_old_behaviour_would_have_covered_everything():
    """Pins what was wrong, so a revert is visible as a failure."""
    assert _rule("*").matches("terminal", "git push --force origin master")
    assert _rule("*").matches("terminal", "rm -rf /")


# --------------------------------------------------------------- the colon trap

def test_the_colon_spelling_from_elsewhere_silently_matches_nothing():
    """``git diff:*`` is the spelling used by other agent tools, and copying it
    here produces a rule that only fires on a literal colon — i.e. never."""
    rule = _rule("git diff:*")
    assert not rule.matches("terminal", "git diff --stat")
    assert rule.matches("terminal", "git diff:--stat")  # what it actually needs


def test_the_never_remember_set_covers_the_permanent_deny_heads():
    for head in TerminalTool.FULL_ACCESS_ALWAYS_DENY_HEADS:
        assert head in NEVER_REMEMBER_PROGRAMS, head
