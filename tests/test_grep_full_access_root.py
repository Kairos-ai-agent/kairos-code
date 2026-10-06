"""Regression: grep must survive a search root that is not under its anchor.

With full access on, the agent may search an absolute path inside the real
work_dir while the tool is anchored to a per-task git worktree. The per-match
display path called ``relative_to()`` on those files; that raises ValueError,
which is neither OSError nor UnicodeError, so it escaped ``execute()`` and the
whole grep died with:

    '<...>\\proj\\.importlinter' is not in the subpath of
    '<...>\\proj\\.kairos-worktrees\\kairos-coder-abc123'

FindTool has always guarded this exact case (kairos/tools/find.py:57-60).
"""
from __future__ import annotations

import asyncio

import pytest

from kairos.tools.grep_tool import GrepTool


@pytest.fixture()
def anchored_project(tmp_path):
    """A project whose tool anchor is a worktree inside it, not the root."""
    proj = tmp_path / "proj"
    worktree = proj / ".kairos-worktrees" / "kairos-coder-abc123"
    worktree.mkdir(parents=True)
    (proj / ".importlinter").write_text("import mypkg\n", encoding="utf-8")
    return proj, worktree


def test_grep_outside_the_anchor_returns_matches(anchored_project, monkeypatch):
    proj, worktree = anchored_project
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")
    tool = GrepTool(allowed_root=str(worktree))

    result = asyncio.run(tool.execute(pattern="import", path=str(proj)))

    assert result.success, result.error
    assert ".importlinter" in result.output


def test_same_directory_stays_relative_when_inside_the_anchor(anchored_project,
                                                              monkeypatch):
    """The normal, non-full-access case is untouched: still project-relative."""
    proj, worktree = anchored_project
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")
    (worktree / "inside.py").write_text("import inside\n", encoding="utf-8")
    tool = GrepTool(allowed_root=str(worktree))

    result = asyncio.run(tool.execute(pattern="import", path=str(worktree)))

    assert result.success, result.error
    assert "inside.py:1:" in result.output
