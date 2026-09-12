"""Regression: a checkpoint must never touch files outside its workspace.

Background — `checkpoint_round` used to run `git -C <workspace> add -A` followed
by a plain `git commit`. When the workspace lives inside a bigger repository
(the normal case is `<repo>/workspace/<project-id>`) git operates on the
repository that *owns* it, so the unscoped `add -A` staged the entire parent
repository. That is how a 640 MB third-party archive ended up in this project's
history, and later how an in-progress working tree got auto-committed as
"kairos: round 1 approved".

The fix resolves the owning repository and scopes both the `git add` and the
`git commit` to the workspace subtree (`kairos/tools/checkpoint.py::_scope`).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from kairos.tools.checkpoint import checkpoint_round


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q"], path)
    _run(["git", "config", "user.email", "test@example.com"], path)
    _run(["git", "config", "user.name", "Test"], path)


def _changed_files(repo: Path, sha: str) -> list[str]:
    out = _run(["git", "show", "--pretty=format:", "--name-only", sha], repo).stdout
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def test_nested_workspace_commit_is_scoped_to_the_workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    # A file that belongs to the *parent* repository and was never committed.
    (repo / "outer.txt").write_text("outer\n", encoding="utf-8")
    nested = repo / "workspace" / "p1"
    nested.mkdir(parents=True)
    (nested / "app.py").write_text("print(1)\n", encoding="utf-8")

    sha = checkpoint_round(nested, round_no=1, score=100,
                           summary="lgtm", approved=True)
    assert sha, "the nested workspace should still get its own checkpoint"

    assert _changed_files(repo, sha) == ["workspace/p1/app.py"]
    # The parent repo's own file was not swept in ...
    assert "outer.txt" not in _run(["git", "ls-files"], repo).stdout.split()
    # ... and is still on disk, untouched.
    assert (repo / "outer.txt").read_text(encoding="utf-8") == "outer\n"


def test_nested_workspace_commit_survives_a_dirty_parent_index(tmp_path: Path):
    """Staged-but-unrelated changes in the parent repo must not be committed."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "outer.txt").write_text("outer\n", encoding="utf-8")
    _run(["git", "add", "-A"], repo)          # deliberately stage the parent file
    nested = repo / "workspace" / "p1"
    nested.mkdir(parents=True)
    (nested / "app.py").write_text("print(1)\n", encoding="utf-8")

    sha = checkpoint_round(nested, round_no=1, score=90,
                           summary="ok", approved=False)
    assert sha
    assert _changed_files(repo, sha) == ["workspace/p1/app.py"]
    # outer.txt is still staged (not committed, not lost).
    assert "outer.txt" in _run(["git", "diff", "--cached", "--name-only"], repo).stdout


def test_repo_root_workspace_still_commits_its_own_tree(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")

    sha = checkpoint_round(repo, round_no=1, score=80, summary="ok", approved=False)
    assert sha
    assert _changed_files(repo, sha) == ["a.py"]


def test_nothing_to_commit_returns_none(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert checkpoint_round(repo, 1, 80, "ok", False) is not None
    assert checkpoint_round(repo, 2, 90, "ok", True) is None
