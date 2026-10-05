"""Allow rules that the gate can never reach are reported, not left to rot.

``check`` consults deny before ask before allow, so a single broad deny makes
every narrower allow for the same tool dead — including the one the user adds
later to stop the prompts. A rule that silently does nothing is worse than no
rule, because the user believes the gate has been told.
"""
from __future__ import annotations

from kairos.permissions import (
    Decision,
    PermissionRule,
    detect_shadowed_rules,
)
from kairos.sentinel import narrowest_pattern


def _rule(spec: str, decision: Decision) -> PermissionRule:
    return PermissionRule.from_str(spec, decision)


def test_a_broad_deny_makes_every_narrower_allow_dead():
    rules = [
        _rule("terminal(git diff*)", Decision.ALLOW),
        _rule("terminal(*)", Decision.DENY),
    ]
    findings = detect_shadowed_rules(rules)

    assert len(findings) == 1
    assert "terminal(git diff*) never fires" in findings[0]
    assert "terminal(*) (deny)" in findings[0]


def test_a_broad_ask_shadows_an_allow_too():
    """An ask is still consulted before any allow, so the allow never decides."""
    rules = [
        _rule("terminal(git diff*)", Decision.ALLOW),
        _rule("terminal(*)", Decision.ASK),
    ]
    assert len(detect_shadowed_rules(rules)) == 1


def test_the_same_pattern_in_two_decisions_is_reported():
    rules = [
        _rule("terminal(git diff*)", Decision.ALLOW),
        _rule("terminal(git diff*)", Decision.DENY),
    ]
    assert len(detect_shadowed_rules(rules)) == 1


def test_a_narrower_deny_does_not_shadow_a_broader_allow():
    """`deny terminal(rm *)` says nothing about `allow terminal(git diff*)`."""
    rules = [
        _rule("terminal(git diff*)", Decision.ALLOW),
        _rule("terminal(rm *)", Decision.DENY),
    ]
    assert detect_shadowed_rules(rules) == []


def test_an_empty_pattern_is_dead_on_arrival():
    assert detect_shadowed_rules([_rule("terminal()", Decision.ALLOW)])


def test_a_wildcard_tool_deny_shadows_other_tools():
    """The ``*`` tool is only reachable from Python — ``from_str`` cannot spell
    it (its pattern for the tool name is word characters and dashes) — but a
    rule built that way still swallows every tool's allows."""
    rules = [
        _rule("write_file(/tmp/*)", Decision.ALLOW),
        PermissionRule(tool="*", pattern="*", decision=Decision.DENY),
    ]
    assert len(detect_shadowed_rules(rules)) == 1


def test_rules_that_can_fire_are_not_reported():
    rules = [
        _rule("terminal(git diff*)", Decision.ALLOW),
        _rule("write_file(*)", Decision.ALLOW),
        _rule("terminal(rm *)", Decision.DENY),
    ]
    assert detect_shadowed_rules(rules) == []


def test_the_live_shape_from_a_remembered_rule():
    """What actually happens: the narrowed allow the gate just wrote is eaten
    by a ``terminal(*)`` deny recorded by hand months earlier."""
    allow = _rule(
        f"terminal({narrowest_pattern('terminal', 'git diff --stat')})",
        Decision.ALLOW,
    )
    findings = detect_shadowed_rules([allow, _rule("terminal(*)", Decision.DENY)])

    assert len(findings) == 1
    assert "git diff*" in findings[0]
