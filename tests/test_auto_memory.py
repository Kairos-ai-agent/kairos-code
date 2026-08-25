"""Tests for auto-learned user preferences."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.learning.auto_memory import (
    MemoryEvent,
    Preference,
    extract_preferences,
    update_agents_md,
)


# ---------------------------------------------------------------------------
# extract_preferences: text messages
# ---------------------------------------------------------------------------


def test_extract_always_pattern():
    events = [
        MemoryEvent("message", {"text": "always run npm install before tests"}, ts=100),
    ]
    prefs = extract_preferences(events)
    assert len(prefs) == 1
    assert "npm install before tests" in prefs[0].statement.lower()


def test_extract_never_pattern():
    events = [
        MemoryEvent("message", {"text": "never use tabs, only spaces"}, ts=200),
    ]
    prefs = extract_preferences(events)
    assert len(prefs) == 1
    assert "tabs" in prefs[0].statement.lower()
    assert "spaces" in prefs[0].statement.lower()


def test_extract_multiple_phrases_in_one_message():
    events = [
        MemoryEvent("message", {
            "text": "always add type hints. never use any.",
        }, ts=100),
    ]
    prefs = extract_preferences(events)
    statements = {p.statement.lower() for p in prefs}
    # Both phrases captured.
    assert any("type hints" in s for s in statements)
    assert any("any" in s for s in statements)


def test_extract_empty_message_yields_no_preference():
    events = [MemoryEvent("message", {"text": ""}, ts=100)]
    assert extract_preferences(events) == []


def test_extract_ignores_chatty_messages():
    """A normal sentence without always/never should produce nothing."""
    events = [
        MemoryEvent("message", {"text": "Can you add a tests directory?"}, ts=100),
    ]
    assert extract_preferences(events) == []


# ---------------------------------------------------------------------------
# extract_preferences: approvals
# ---------------------------------------------------------------------------


def test_extract_approval_allow():
    events = [
        MemoryEvent("approval", {
            "tool": "terminal", "resource": "git status",
            "decision": "allow",
        }, ts=100),
    ]
    prefs = extract_preferences(events)
    assert len(prefs) == 1
    assert "terminal" in prefs[0].statement
    assert "git status" in prefs[0].statement


def test_extract_approval_deny_is_higher_weight():
    allow = MemoryEvent("approval", {
        "tool": "terminal", "resource": "rm",
        "decision": "allow",
    }, ts=100)
    deny = MemoryEvent("approval", {
        "tool": "terminal", "resource": "rm",
        "decision": "deny",
    }, ts=100)
    pa = extract_preferences([allow])[0].weight
    pd = extract_preferences([deny])[0].weight
    assert pd > pa  # deny weighted higher


# ---------------------------------------------------------------------------
# extract_preferences: merging
# ---------------------------------------------------------------------------


def test_extract_merges_duplicate_statements():
    events = [
        MemoryEvent("message", {"text": "always use type hints"}, ts=100),
        MemoryEvent("message", {"text": "always use type hints"}, ts=200),
        MemoryEvent("message", {"text": "always use type hints"}, ts=300),
    ]
    prefs = extract_preferences(events)
    assert len(prefs) == 1
    assert prefs[0].evidence_count == 3
    # Repeated observations strengthen the signal.
    assert prefs[0].weight > 2.0


def test_extract_sorted_by_weight_then_evidence():
    events = [
        # Strong: correction (3.0)
        MemoryEvent("correction", {
            "path": "x.py", "before": "old", "after": "Always add docstrings",
        }, ts=100),
        # Weak: single allow (1.0)
        MemoryEvent("approval", {
            "tool": "file_read", "resource": "y", "decision": "allow",
        }, ts=100),
    ]
    prefs = extract_preferences(events)
    assert "docstrings" in prefs[0].statement
    assert "file_read" in prefs[1].statement


def test_extract_skips_oversized_statements():
    events = [
        MemoryEvent("message", {
            "text": "always " + ("x" * 250),
        }, ts=100),
    ]
    # 200-char cap on statement length, so this is dropped.
    assert extract_preferences(events) == []


# ---------------------------------------------------------------------------
# update_agents_md
# ---------------------------------------------------------------------------


def test_update_creates_file_when_missing(tmp_path):
    out = tmp_path / "AGENTS.md"
    prefs = [Preference(statement="use type hints", weight=1.0, evidence_count=1)]
    n = update_agents_md(prefs, out)
    assert n == 1
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "## Auto-learned preferences" in text
    assert "use type hints" in text


def test_update_preserves_user_content_above_section(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "# Project\n\n## Architecture\n- Uses Flask.\n",
        encoding="utf-8",
    )
    prefs = [Preference(statement="use type hints", weight=1.0)]
    update_agents_md(prefs, f)
    text = f.read_text(encoding="utf-8")
    # User content untouched.
    assert "## Architecture" in text
    assert "Uses Flask" in text
    # Our section appended.
    assert "## Auto-learned preferences" in text


def test_update_overwrites_own_section_on_repeat(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "# Project\n\n## Auto-learned preferences\n- old pref\n",
        encoding="utf-8",
    )
    prefs = [Preference(statement="new pref", weight=1.0)]
    update_agents_md(prefs, f)
    text = f.read_text(encoding="utf-8")
    assert "old pref" not in text
    assert "new pref" in text


def test_update_with_empty_prefs_clears_section(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "# Project\n\n## Auto-learned preferences\n- old pref\n",
        encoding="utf-8",
    )
    n = update_agents_md([], f)
    assert n == 0
    text = f.read_text(encoding="utf-8")
    # Section removed.
    assert "old pref" not in text
    assert "## Auto-learned preferences" not in text
    # User's "Project" line preserved.
    assert "# Project" in text


def test_update_idempotent_when_no_change(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "# Project\n\n## Auto-learned preferences\n- p1\n",
        encoding="utf-8",
    )
    # Empty preferences AND a section exists → strip section.
    update_agents_md([], f)
    text1 = f.read_text(encoding="utf-8")
    update_agents_md([], f)
    text2 = f.read_text(encoding="utf-8")
    assert text1 == text2
