"""Bundled skills must not promise tools this app does not ship.

547 of the bundled skills were imported from other harnesses. Some of them tell
the model to call a tool that does not exist here. The worst case was
`computer-use`: it told every model "You have a `computer_use` tool that drives
the user's desktop" while the toolset had twelve tools and none of them was
that one -- and it taught a driver, a capture mode and an element index that
this project has never had.

Most of the remaining entries are skills *about* another agent ("how to write a
Claude Code hook"), where naming `Task()` or `TodoWrite` is correct and useful.
Rewriting 29 skills is a different job from fixing one broken promise, so this
test freezes the set instead: a new import that names a foreign tool fails CI,
so shipping it is a decision rather than a discovery. Delete a line from the
baseline when you clean a skill up; the test only fails on *additions*.
"""
from __future__ import annotations

from pathlib import Path

# Parameter and tool names that belong to another harness. Deliberately narrow:
# `computer_use(action="capture")` is how OUR tool is called, so the bare action
# is not a marker -- `mode="som"`, `element=` and `cua-driver` are.
FOREIGN_MARKERS = (
    # Hermes
    'mode="som"', "cua-driver", "capture_after=", "delivery_mode=",
    "list_apps", "focus_app", "element=",
    # Claude Code
    "TodoWrite", "AskUserQuestion", "NotebookEdit", "SlashCommand",
    "mcp__", "str_replace_editor", "WebFetch(", "Task(", "Bash(", "Read(", "Edit(",
)

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "kairos" / "skills"
BASELINE_FILE = Path(__file__).resolve().parent / "data" / \
    "foreign_tool_vocabulary_baseline.txt"


def _offenders() -> set:
    found = set()
    for path in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in FOREIGN_MARKERS):
            found.add(path.parent.name + "/SKILL.md")
    return found


def _baseline() -> set:
    lines = BASELINE_FILE.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def test_the_baseline_file_is_present_and_readable():
    assert BASELINE_FILE.exists(), BASELINE_FILE
    assert len(_baseline()) > 0


def test_no_new_bundled_skill_names_a_tool_we_do_not_ship():
    new = sorted(_offenders() - _baseline())
    assert not new, (
        "these bundled skills reference a tool this app does not ship:\n  "
        + "\n  ".join(new)
        + "\n\nFix the skill to describe the tool that exists, or add it to "
        + str(BASELINE_FILE.name) + " deliberately."
    )


def test_the_baseline_does_not_rot():
    """Every line still has to be a real offender, so cleaning up narrows it."""
    stale = sorted(_baseline() - _offenders())
    assert not stale, (
        "these skills no longer reference a foreign tool; delete them from "
        + BASELINE_FILE.name + ":\n  " + "\n  ".join(stale)
    )


# ---------------------------------------------------------------------------
# the skill that was wrong
# ---------------------------------------------------------------------------

def test_computer_use_documents_the_tool_that_exists():
    text = (SKILLS_ROOT / "computer-use" / "SKILL.md").read_text(encoding="utf-8")
    # It must name the actions the tool actually implements...
    for action in ("capture", "click", "type", "key", "scroll", "history",
                   "screen_size"):
        assert action in text, action
    # ...say which backend ran, which is how the model learns a mock did nothing...
    assert "MockComputerUse" in text
    assert "PlatformComputerUse" in text
    # ...and not promise background input it cannot deliver.
    assert "not a background driver" in text
    # The Hermes vocabulary must be gone.
    assert "cua-driver" not in text
    assert 'mode="som"' not in text
    assert "capture_after" not in text


def test_the_skill_action_list_matches_the_tool_schema():
    """A skill and a tool that drift apart is how the last one went wrong."""
    from kairos.tools.computer_tool import ACTIONS

    text = (SKILLS_ROOT / "computer-use" / "SKILL.md").read_text(encoding="utf-8")
    for action in ACTIONS:
        assert "`" + action + "`" in text, action + " is missing from the skill"


def test_imported_skills_that_name_another_agent_say_so():
    """The three that promise a desktop/browser tool carry a reality check."""
    for name in ("macos-computer-use", "computer-interface-controller",
                 "control-in-app-browser"):
        text = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        assert "Reality check" in text, name
