"""The Coder prompt is a general-purpose agent, not a self-limited code assistant.

The user's complaint: the agent behaved like a Python-style code assistant with
its own handbrake on — it was told to make the "smallest change", never to
restart the loop, and to answer the first round with only a plain-text plan
before it was allowed to touch anything. Those were prompt-level self-limits, not
permission limits (fullAccess is already on), so the real ceiling was the wording.

After the change the prompt (a) carries no "first round is plan-only" gate, (b)
states plainly that it does not assume the project type / language / toolchain,
and (c) still refuses the handful of disaster moves — push to a remote, delete the
user's home dir, run destructive commands — while the credential boundary stays
enforced in code (:mod:`kairos.sentinel`).

Each of these can regress on its own, so each is pinned separately.
"""
from __future__ import annotations

from pathlib import Path

from kairos.agents.roles.coder import SYSTEM_PROMPT

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# (a) the "first round is plan-only" hard gate is gone
# ---------------------------------------------------------------------------


def test_no_plan_first_hard_gate_in_prompt():
    """The prompt must not tell the agent to open with a plan and hold the tools.

    The old "## Plan mode (first round only)" section made a plain-text plan the
    expected first move. It is deleted; a soft "look before a big change" hint is
    fine, a hard gate is not.
    """
    text = SYSTEM_PROMPT
    assert "Plan mode (first round only)" not in text
    assert "only a plain-text plan" not in text
    # The strongest form of the gate: "no tool calls" on the first turn.
    assert "No tool calls." not in text
    assert "No JSON wrappers" not in text


def test_prompt_is_not_a_code_only_persona():
    """The old opening framed the agent as a *software engineer* only."""
    text = SYSTEM_PROMPT
    assert "senior software engineer" not in text
    assert "general-purpose agent" in text


# ---------------------------------------------------------------------------
# (b) it says plainly that it does not assume the project
# ---------------------------------------------------------------------------


def test_prompt_declares_it_does_not_assume_the_project_type():
    text = SYSTEM_PROMPT
    # The negative is explicit ...
    assert "Do not assume what kind of project this is" in text
    assert "not be a software project" in text
    # ... and it names the kinds of work, so "general" is concrete not vague.
    for kind in ("writing", "design", "data analysis", "operations",
                 "DevOps", "documentation", "research"):
        assert kind in text, f"scope list is missing {kind!r}"


def test_prompt_asks_for_reconnaissance_before_action():
    text = SYSTEM_PROMPT
    # No assumed language / framework / toolchain: look first.
    assert "language or framework" in text
    assert "toolchain" in text
    assert "Reconnoiter before you act" in text


def test_fix_needed_not_smallest_change():
    """"Smallest change" is replaced by "make the change the problem needs",
    with refactoring allowed if you say why."""
    text = SYSTEM_PROMPT
    assert "Smallest change that solves the problem" not in text
    assert "refactor when the problem requires it" in text
    assert "say why" in text


# ---------------------------------------------------------------------------
# (c) the disaster boundaries stay
# ---------------------------------------------------------------------------


def test_accident_boundaries_still_in_prompt():
    text = SYSTEM_PROMPT
    # push to a remote is still refused
    assert "push to git remotes" in text
    assert "delete the user's home dir" in text
    assert "destructive" in text
    # and the "answer is in the codebase, don't ask" rule survives
    assert "answer is in the codebase" in text


def test_credential_boundary_still_enforced_in_code():
    """Credentials are blocked by the sentinel (a code guard), not by the prompt.

    The prompt no longer needs to restate it, but the guard must still exist —
    otherwise relaxing the prompt would silently relax the boundary.
    """
    from kairos.sentinel import CREDENTIAL_PATH_MARKERS

    assert ".ssh/id_rsa" in CREDENTIAL_PATH_MARKERS
    assert any("git-credentials" in m for m in CREDENTIAL_PATH_MARKERS)
    assert any(".aws" in m for m in CREDENTIAL_PATH_MARKERS)
    # The deny classification the guard emits.
    sentinel_src = (REPO_ROOT / "kairos" / "sentinel.py").read_text(encoding="utf-8")
    assert "credential-store" in sentinel_src


def test_no_loop_as_a_forbidden_option():
    """The prompt tells the agent not to build *its own* loop — it does not ban
    restarting the task, which was the old over-constraint."""
    text = SYSTEM_PROMPT
    assert "Don't restart the loop." not in text
    assert "build your own loop" in text
