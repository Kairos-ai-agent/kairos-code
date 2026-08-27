"""Tests for kairos.tui (Round 18 Textual TUI)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

from kairos.tui_textual import (
    _HAS_TEXTUAL,
    _load_cost_log,
    _render_cost_table,
    _render_skills_table,
    _render_recent_log,
    main,
)


# ---------------------------------------------------------------------------
# Conditional skip: these tests run even without textual so the
# rendering functions can be unit-tested in any environment.
# ---------------------------------------------------------------------------


@pytest.fixture
def cost_log_with_data(tmp_path: Path) -> Path:
    """A cost.jsonl with 3 entries (2 models)."""
    log = tmp_path / "cost.jsonl"
    log.write_text(
        json.dumps({"timestamp": 100.0, "model": "gpt-4o",
                    "provider": "openai", "prompt_tokens": 100,
                    "completion_tokens": 50, "cost_usd": 0.001,
                    "duration_ms": 200}) + "\n" +
        json.dumps({"timestamp": 200.0, "model": "gpt-4o",
                    "provider": "openai", "prompt_tokens": 200,
                    "completion_tokens": 100, "cost_usd": 0.002,
                    "duration_ms": 300}) + "\n" +
        json.dumps({"timestamp": 300.0, "model": "claude-3-5-sonnet",
                    "provider": "anthropic", "prompt_tokens": 50,
                    "completion_tokens": 25, "cost_usd": 0.005,
                    "duration_ms": 400}) + "\n",
        encoding="utf-8",
    )
    return log


# ---------------------------------------------------------------------------
# _load_cost_log
# ---------------------------------------------------------------------------


def test_load_cost_log_returns_entries_newest_first(cost_log_with_data: Path):
    entries = _load_cost_log(cost_log_with_data)
    assert len(entries) == 3
    # Newest first
    assert entries[0]["model"] == "claude-3-5-sonnet"
    assert entries[-1]["model"] == "gpt-4o"


def test_load_cost_log_missing_file_returns_empty(tmp_path: Path):
    entries = _load_cost_log(tmp_path / "does-not-exist.jsonl")
    assert entries == []


def test_load_cost_log_corrupt_lines_skipped(tmp_path: Path):
    log = tmp_path / "bad.jsonl"
    log.write_text('{"good": true}\n{not json\n{"also": "good"}\n',
                   encoding="utf-8")
    entries = _load_cost_log(log)
    assert len(entries) == 2


def test_load_cost_log_respects_path_env(tmp_path: Path, monkeypatch):
    """When no path is given, the KAIROS_DATA_DIR env var wins."""
    log = tmp_path / "cost.jsonl"
    log.write_text('{"model": "m", "cost_usd": 0.1, "prompt_tokens": 1, '
                   '"completion_tokens": 1, "timestamp": 0, "duration_ms": 1}\n',
                   encoding="utf-8")
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    entries = _load_cost_log()
    assert len(entries) == 1


# ---------------------------------------------------------------------------
# _render_cost_table
# ---------------------------------------------------------------------------


def test_render_cost_table_empty():
    out = _render_cost_table([])
    assert "No cost data" in out


def test_render_cost_table_aggregates_by_model(cost_log_with_data: Path):
    entries = _load_cost_log(cost_log_with_data)
    out = _render_cost_table(entries)
    # Both models should be mentioned
    assert "gpt-4o" in out
    assert "claude-3-5-sonnet" in out
    # The header is present
    assert "Model" in out or "Calls" in out
    # Total line
    assert "Total:" in out
    # Total cost: 0.001 + 0.002 + 0.005 = 0.008
    assert "0.008000" in out or "0.00800" in out or "8e-0" in out or "0.008" in out


def test_render_cost_table_sorts_by_cost_desc(cost_log_with_data: Path):
    entries = _load_cost_log(cost_log_with_data)
    out = _render_cost_table(entries)
    # claude (0.005) should appear before gpt-4o (0.003) in the output
    assert out.index("claude-3-5-sonnet") < out.index("gpt-4o")


# ---------------------------------------------------------------------------
# _render_skills_table
# ---------------------------------------------------------------------------


class _FakeSkill:
    def __init__(self, name: str, body: str = "", priority: float = 0.5,
                 source_path: str = "/fake.md"):
        self.name = name
        self.body = body
        self.priority = priority
        self.source_path = source_path


def test_render_skills_table_empty():
    out = _render_skills_table([])
    assert "No skills" in out


def test_render_skills_table_lists_all():
    skills = [
        _FakeSkill("a", body="alpha body"),
        _FakeSkill("b", body="beta body", priority=0.9),
    ]
    out = _render_skills_table(skills)
    assert "2 skills" in out
    assert "alpha" in out
    assert "beta" in out


def test_render_skills_table_filter_by_query():
    skills = [
        _FakeSkill("pytest", body="use pytest for tests"),
        _FakeSkill("react", body="react stuff"),
    ]
    out = _render_skills_table(skills, query="pytest")
    assert "pytest" in out
    assert "react" not in out


def test_render_skills_table_no_match():
    skills = [_FakeSkill("a", body="alpha")]
    out = _render_skills_table(skills, query="xyzzz")
    assert "No skills match" in out


def test_render_skills_table_truncates_at_20():
    """A list of 30 skills shows the count but only 20 entries."""
    skills = [_FakeSkill(f"s{i}") for i in range(30)]
    out = _render_skills_table(skills)
    assert "30 skills" in out
    # The body lines are capped at 20; the names start with "s0" through "s19"
    assert "s19" in out
    assert "s29" not in out  # capped


# ---------------------------------------------------------------------------
# _render_recent_log
# ---------------------------------------------------------------------------


def test_render_recent_log_empty():
    out = _render_recent_log([])
    assert "No calls" in out


def test_render_recent_log_lists_entries(cost_log_with_data: Path):
    entries = _load_cost_log(cost_log_with_data)
    out = _render_recent_log(entries)
    # Newest first
    assert "claude-3-5-sonnet" in out
    assert "gpt-4o" in out


def test_render_recent_log_caps_at_30(cost_log_with_data: Path):
    # Build a 50-entry log from scratch (no seed entries)
    entries = []
    for i in range(50):
        entries.append({
            "timestamp": 1000 + i, "model": f"m{i:02d}", "cost_usd": 0.0001,
            "prompt_tokens": 1, "completion_tokens": 1, "duration_ms": 1,
        })
    out = _render_recent_log(entries)
    # The renderer is capped at 30 (so the highest-numbered m is m29)
    assert "m00" in out
    assert "m29" in out
    assert "m30" not in out


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


def test_main_returns_1_when_textual_missing(monkeypatch):
    """If textual isn't installed, main() returns 1 and prints a message."""
    from kairos import tui_textual as tui_mod
    monkeypatch.setattr(tui_mod, "_HAS_TEXTUAL", False)
    import sys
    captured_err = []
    monkeypatch.setattr(sys, "stderr", type("S", (), {
        "write": lambda s, x: captured_err.append(x)
    })())
    rc = main([])
    assert rc == 1
    assert any("Textual" in s for s in captured_err)


def test_has_textual_is_bool():
    """The flag is a real bool (so the entrypoint check works)."""
    assert isinstance(_HAS_TEXTUAL, bool)
