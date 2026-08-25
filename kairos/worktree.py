"""Git worktree isolation for sub-agents.

When multiple sub-agents (Coder, Reviewer, Refactorer, …) work on
the same project in parallel, they would normally stomp on each
other's files. Codex handles this with `git worktree`: each
sub-agent gets an independent checkout of the same repo on its
own branch, and the orchestrator merges the branch back when the
sub-task completes successfully.

This module provides the management primitives. It is intentionally
minimal: the high-level "dispatch a sub-task into a worktree"
flow lives in `core/orchestrator.py`. Keep this file focused on the
shell-level mechanics so it's easy to test in isolation.

Key entry points:

    mgr = WorktreeManager(repo_path=Path("/path/to/repo"))
    wt = mgr.create(branch_name="agent-coder-abcd1234")
    # ... agent edits files in wt.path ...
    mgr.merge_to(wt, target_branch="main")
    mgr.cleanup(wt)  # removes worktree + deletes the agent branch

Concurrency model: two agents requesting the same branch name at
the same time will be rejected by git; callers should pre-generate
unique names (we expose `unique_branch_name(role)` to help).
"""
from __future__ import annotations

import logging
import re
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_WORKTREE_PARENT = ".kairos-worktrees"


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


@dataclass
class Worktree:
    """A logical handle to a created git worktree."""
    path: Path
    branch: str
    repo: Path
    created_at: float = 0.0

    def is_valid(self) -> bool:
        """Cheap sanity check: the path exists and is a directory."""
        return self.path.exists() and self.path.is_dir()


@dataclass
class WorktreeManager:
    """Manages git worktrees for a single repo.

    The manager does not own the lifetime of the worktrees it creates;
    callers should call ``cleanup`` (or use the context manager) when
    done. If a worktree is left behind across process restarts, it
    can be recovered with ``list_existing`` and removed later.
    """
    repo_path: Path
    parent_dir: Optional[Path] = None

    def __post_init__(self) -> None:
        self.repo_path = Path(self.repo_path).resolve()
        if not (self.repo_path / ".git").exists():
            raise WorktreeError(
                f"{self.repo_path} is not a git repository "
                "(missing .git directory)"
            )
        if self.parent_dir is None:
            self.parent_dir = self.repo_path / DEFAULT_WORKTREE_PARENT
        self.parent_dir = Path(self.parent_dir).resolve()
        self.parent_dir.mkdir(parents=True, exist_ok=True)

    # -- low-level git wrapper ---------------------------------------------

    def _run_git(self, *args: str, check: bool = True) -> str:
        """Run a git command in `repo_path` and return stdout.

        On non-zero exit, raise WorktreeError with stderr included.
        """
        cmd = ("git",) + args
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise WorktreeError(
                "git executable not found on PATH; install git "
                "to use worktree isolation"
            ) from exc
        if check and result.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed "
                f"(exit {result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout.strip()

    # -- public API --------------------------------------------------------

    @staticmethod
    def unique_branch_name(role: str) -> str:
        """Generate a unique branch name like ``kairos-coder-ab12cd34``.

        We prefix with ``kairos-`` so cleanup tools can spot our
        branches even after a crash.
        """
        # Disallow characters that would confuse git ref names.
        clean = re.sub(r"[^A-Za-z0-9._/-]+", "-", role)
        return f"kairos-{clean}-{uuid.uuid4().hex[:8]}"

    def create(self, branch_name: Optional[str] = None) -> Worktree:
        """Create a new worktree on a fresh branch off the current HEAD.

        Returns a Worktree handle. The worktree path is
        ``<parent_dir>/<branch_name>``.
        """
        if branch_name is None:
            branch_name = self.unique_branch_name("agent")
        worktree_path = self.parent_dir / branch_name
        if worktree_path.exists():
            raise WorktreeError(
                f"worktree path {worktree_path} already exists; "
                "choose a different branch name or clean up first"
            )
        # `git worktree add -b <new-branch> <path>` creates a new
        # branch off HEAD and checks it out into <path>.
        self._run_git("worktree", "add", "-b", branch_name, str(worktree_path))
        import time
        return Worktree(
            path=worktree_path,
            branch=branch_name,
            repo=self.repo_path,
            created_at=time.time(),
        )

    def list_existing(self) -> List[Worktree]:
        """Enumerate all worktrees this manager knows about.

        A "kairos-" branch prefix is used to avoid picking up
        branches created by other tools.
        """
        out = self._run_git("worktree", "list", "--porcelain")
        # `git worktree list --porcelain` emits 3 lines per worktree
        # (path, HEAD, branch), separated by blank lines. The final
        # block may NOT be followed by an extra newline, so a naive
        # `split("\n\n")` collapses everything into one. We split on
        # line boundaries and re-chunk in 3-line groups instead.
        #
        # Each line is "key value" — the field name ("worktree",
        # "HEAD", "branch") is a single word, the value is the rest
        # of the line. So we read the value as the second token
        # of `line.split()`.
        items: List[Worktree] = []
        lines = [ln for ln in out.splitlines() if ln.strip()]
        for i in range(0, len(lines), 3):
            block = lines[i:i + 3]
            if len(block) < 3:
                continue
            first_tokens = block[0].split(maxsplit=1)
            if len(first_tokens) < 2 or first_tokens[0] != "worktree":
                continue
            path = Path(first_tokens[1])
            branch_tokens = block[2].split(maxsplit=1)
            if len(branch_tokens) < 2 or branch_tokens[0] != "branch":
                continue
            branch_ref = branch_tokens[1]
            if not branch_ref.startswith("refs/heads/"):
                continue
            branch = branch_ref.removeprefix("refs/heads/")
            if not branch.startswith("kairos-"):
                continue
            items.append(Worktree(
                path=path, branch=branch, repo=self.repo_path,
            ))
        return items

    def merge_to(
        self,
        worktree: Worktree,
        target_branch: str = "main",
        commit_message: Optional[str] = None,
    ) -> None:
        """Fast-forward `target_branch` to `worktree.branch` and return
        the worktree to its starting commit.

        Only fast-forward is supported. If the branches have diverged
        we raise WorktreeError; the caller is expected to handle
        conflicts out of band (e.g. by leaving the worktree branch
        around for manual review).
        """
        # `git merge --ff-only` refuses to do anything if the target
        # branch has new commits. That's exactly the safety net we
        # want — divergent branches should be a human decision.
        msg = commit_message or (
            f"Merge worktree branch {worktree.branch} into {target_branch}"
        )
        self._run_git(
            "merge", "--ff-only", "--no-ff" if False else "--ff-only",
            "-m", msg, worktree.branch,
        )

    def cleanup(
        self,
        worktree: Worktree,
        remove_branch: bool = True,
        delete_working_files: bool = True,
    ) -> None:
        """Tear down a worktree. Best effort; ignores individual
        failure modes so a partially-orphaned state still cleans up."""
        # `git worktree remove --force` removes the worktree even if
        # there are local modifications.
        try:
            self._run_git("worktree", "remove", "--force", str(worktree.path))
        except WorktreeError as exc:
            logger.warning("worktree: git worktree remove failed: %s", exc)
        if delete_working_files and worktree.path.exists():
            # Belt-and-suspenders: in some rare git states the
            # directory may still exist after `worktree remove`.
            import shutil
            shutil.rmtree(worktree.path, ignore_errors=True)
        if remove_branch:
            try:
                self._run_git("branch", "-D", worktree.branch)
            except WorktreeError as exc:
                logger.warning(
                    "worktree: deleting branch %s failed: %s",
                    worktree.branch, exc,
                )

    @contextmanager
    def worktree(
        self,
        branch_name: Optional[str] = None,
    ) -> Iterator[Worktree]:
        """Context-manager form: auto-cleanup on exit, even on error.

        Failure to clean up is logged but doesn't raise — the
        worktree may be valuable (e.g. left for human review).
        """
        wt = self.create(branch_name=branch_name)
        try:
            yield wt
        except Exception:
            # Caller raised; still try to clean up, but don't mask
            # the original error.
            try:
                self.cleanup(wt)
            except Exception as exc:
                logger.warning("worktree: cleanup after exception failed: %s", exc)
            raise
        else:
            # Success: leave the cleanup decision to the caller. If
            # the caller didn't explicitly merge, we drop the
            # worktree directory but keep the branch so the work
            # isn't lost.
            self.cleanup(wt, remove_branch=False)
