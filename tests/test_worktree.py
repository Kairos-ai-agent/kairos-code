"""Tests for git worktree isolation."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.worktree import (
    Worktree,
    WorktreeError,
    WorktreeManager,
)


# ---------------------------------------------------------------------------
# Fixtures: create a tiny git repo we can spin worktrees on.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    """Run a git command and return stdout; raise WorktreeError-ish
    on non-zero exit so failures are visible."""
    res = subprocess.run(
        ("git",) + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and res.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}: {res.stderr.strip()}"
        )
    return res.stdout.strip()


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a fresh git repo with one commit on `main`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("initial", encoding="utf-8")
    # Cross-platform git identity so commits don't fail in CI.
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial commit")
    return repo


def _has_git() -> bool:
    return shutil.which("git") is not None


@pytest.fixture
def git_repo(tmp_path) -> Path:
    if not _has_git():
        pytest.skip("git executable not on PATH")
    return _make_git_repo(tmp_path)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_manager_requires_git_repo(tmp_path):
    (tmp_path / "not-a-repo").mkdir()
    with pytest.raises(WorktreeError) as exc:
        WorktreeManager(repo_path=tmp_path / "not-a-repo")
    assert "not a git repository" in str(exc.value)


def test_manager_creates_default_parent_dir(git_repo):
    mgr = WorktreeManager(repo_path=git_repo)
    assert mgr.parent_dir.exists()
    assert mgr.parent_dir == git_repo / ".kairos-worktrees"


def test_manager_accepts_custom_parent_dir(git_repo, tmp_path):
    custom = tmp_path / "my-worktrees"
    mgr = WorktreeManager(repo_path=git_repo, parent_dir=custom)
    assert mgr.parent_dir == custom.resolve()


# ---------------------------------------------------------------------------
# unique_branch_name
# ---------------------------------------------------------------------------


def test_unique_branch_name_format():
    name = WorktreeManager.unique_branch_name("coder")
    assert name.startswith("kairos-coder-")
    # 8 hex chars after the dash.
    assert len(name.split("-")[-1]) == 8


def test_unique_branch_name_sanitizes_special_chars():
    name = WorktreeManager.unique_branch_name("with spaces & symbols!")
    # Spaces and ! both replaced with a single dash.
    assert " " not in name
    assert "!" not in name
    assert name.startswith("kairos-")


# ---------------------------------------------------------------------------
# create / cleanup
# ---------------------------------------------------------------------------


def test_create_worktree(git_repo):
    mgr = WorktreeManager(repo_path=git_repo)
    wt = mgr.create(branch_name="kairos-coder-test1")
    assert wt.path.exists()
    assert wt.branch == "kairos-coder-test1"
    # git recognizes it as a worktree
    listed = mgr.list_existing()
    assert any(w.path == wt.path for w in listed)
    # cleanup
    mgr.cleanup(wt)


def test_create_worktree_with_unique_name(git_repo):
    mgr = WorktreeManager(repo_path=git_repo)
    wt = mgr.create()  # auto-generate
    assert wt.branch.startswith("kairos-")
    mgr.cleanup(wt)


def test_create_rejects_existing_path(git_repo):
    mgr = WorktreeManager(repo_path=git_repo)
    wt1 = mgr.create(branch_name="kairos-1")
    try:
        with pytest.raises(WorktreeError):
            mgr.create(branch_name="kairos-1")  # same branch, same path
    finally:
        mgr.cleanup(wt1)


def test_worktree_isolates_file_changes(git_repo):
    """Edits in the worktree must not affect the main checkout."""
    mgr = WorktreeManager(repo_path=git_repo)
    wt = mgr.create(branch_name="kairos-edit")
    try:
        # Write a new file in the worktree
        (wt.path / "new.txt").write_text("hello", encoding="utf-8")
        # The main repo must NOT see it
        assert not (git_repo / "new.txt").exists()
    finally:
        mgr.cleanup(wt)


def test_worktree_sees_initial_files(git_repo):
    """The worktree must have the same initial state as main."""
    mgr = WorktreeManager(repo_path=git_repo)
    wt = mgr.create(branch_name="kairos-view")
    try:
        assert (wt.path / "README.md").read_text(encoding="utf-8") == "initial"
    finally:
        mgr.cleanup(wt)


def test_worktree_branch_diverge(git_repo):
    """A commit on the worktree branch must be reachable from that
    branch but not from main."""
    mgr = WorktreeManager(repo_path=git_repo)
    wt = mgr.create(branch_name="kairos-commit")
    try:
        (wt.path / "new.txt").write_text("from worktree", encoding="utf-8")
        _git(wt.path, "add", "new.txt")
        _git(wt.path, "commit", "-q", "-m", "add new.txt")
        # The branch has the file; main does not.
        branch_has = _git(git_repo, "show", "kairos-commit:new.txt").strip()
        main_has = _git(git_repo, "show", "main:new.txt", check=False)
        assert branch_has == "from worktree"
        assert main_has == ""  # git show on a missing file returns empty
    finally:
        mgr.cleanup(wt)


# ---------------------------------------------------------------------------
# merge_to
# ---------------------------------------------------------------------------


def test_merge_to_fast_forward(git_repo):
    """A worktree branch that adds a commit should fast-forward main."""
    mgr = WorktreeManager(repo_path=git_repo)
    wt = mgr.create(branch_name="kairos-merge")
    try:
        (wt.path / "merged.txt").write_text("merged", encoding="utf-8")
        _git(wt.path, "add", "merged.txt")
        _git(wt.path, "commit", "-q", "-m", "merge me")
        mgr.merge_to(wt, target_branch="main")
        # main now has merged.txt
        assert (git_repo / "merged.txt").exists()
        assert (git_repo / "merged.txt").read_text(encoding="utf-8") == "merged"
    finally:
        mgr.cleanup(wt)


def test_merge_to_refuses_diverged_branches(git_repo):
    """If main has commits the worktree doesn't, --ff-only refuses."""
    mgr = WorktreeManager(repo_path=git_repo)
    wt = mgr.create(branch_name="kairos-diverged")
    try:
        # Advance main independently
        (git_repo / "main_only.txt").write_text("only on main", encoding="utf-8")
        _git(git_repo, "add", "main_only.txt")
        _git(git_repo, "commit", "-q", "-m", "main commit")
        # And add a commit on the worktree
        (wt.path / "wt_only.txt").write_text("only on wt", encoding="utf-8")
        _git(wt.path, "add", "wt_only.txt")
        _git(wt.path, "commit", "-q", "-m", "wt commit")
        with pytest.raises(WorktreeError):
            mgr.merge_to(wt, target_branch="main")
    finally:
        mgr.cleanup(wt)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


def test_context_manager_creates_and_cleans_up(git_repo):
    with WorktreeManager(repo_path=git_repo).worktree(branch_name="kairos-cm") as wt:
        assert wt.path.exists()
        (wt.path / "ctx.txt").write_text("ctx", encoding="utf-8")
    # After exit, the worktree directory should be gone, but
    # (per WorktreeManager.worktree semantics) the branch is kept.
    assert not wt.path.exists()
    # Branch should still exist.
    branches = _git(git_repo, "branch", "--list", "kairos-cm")
    assert "kairos-cm" in branches


def test_context_manager_cleanup_runs_on_exception(git_repo):
    mgr = WorktreeManager(repo_path=git_repo)
    with pytest.raises(RuntimeError, match="boom"):
        with mgr.worktree(branch_name="kairos-boom") as wt:
            assert wt.path.exists()
            raise RuntimeError("boom")
    assert not wt.path.exists()
