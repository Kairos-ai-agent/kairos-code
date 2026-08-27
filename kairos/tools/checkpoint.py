"""Checkpoint tool + helper functions.

A checkpoint is a git commit tied to a LoopReview round. The loop
auto-commits after every round so the user can roll back to any prior
state from the UI. The tool itself is exposed to the Coder for
explicit "I want to commit now" moments, but in normal operation the
orchestrator handles checkpointing automatically.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

def _git(args: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command. Returns (rc, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out"
    except FileNotFoundError:
        return 127, "", "git not installed"
    except Exception as e:
        return 1, "", str(e)

def ensure_repo(workspace: Path) -> bool:
    """Make sure `workspace` is a git repo. Initializes one if needed.

    Returns True on success. Idempotent: safe to call every round.
    """
    workspace = Path(workspace)
    if not workspace.exists():
        return False
    rc, _, _ = _git(["rev-parse", "--git-dir"], workspace)
    if rc == 0:
        return True
    rc, _, err = _git(["init", "-q"], workspace)
    if rc != 0:
        logger.warning("git init failed for %s: %s", workspace, err)
        return False
    # Ensure a local user identity exists so commits don't blow up on
    # minimal CI environments.
    _git(["config", "user.email", "kairos@localhost"], workspace)
    _git(["config", "user.name", "Kairos Coder"], workspace)
    return True

def checkpoint_round(workspace: Path, round_no: int, score: int,
                     summary: str, approved: bool,
                     plan: Optional[dict] = None) -> Optional[str]:
    """Auto-checkpoint a round: stage everything, commit, return SHA.

    No-op (returns None) if there's nothing to commit or git fails.
    The commit message is structured so the UI can parse it back.

    Round 12: ``plan`` (a JSON-safe dict from
    ``Plan.to_dict()``) is appended to the commit message so the
    git history doubles as a Plan history. ``git log`` can be
    parsed back to reconstruct the agent's plan at any past
    commit.
    """
    workspace = Path(workspace)
    if not ensure_repo(workspace):
        return None
    _git(["add", "-A"], workspace)
    # `git diff --cached --quiet` exits 0 if nothing staged, 1 if changes.
    rc, _, _ = _git(["diff", "--cached", "--quiet"], workspace)
    if rc == 0:
        return None
    verdict = "approved" if approved else "rejected"
    msg = (
        f"kairos: round {round_no} {verdict} (score {score})\n\n"
        f"{summary[:200]}"
    )
    if plan and isinstance(plan, dict) and plan.get("todos"):
        # Render the plan as a Markdown block so it's grep-friendly
        # from the command line.
        from kairos.loop.plan import render_plan_block
        from kairos.loop.plan import Plan
        try:
            block = render_plan_block(Plan.from_dict(plan))
            if block:
                msg = msg + "\n\n# Plan at this round\n\n" + block
        except Exception:
            # Plan rendering is best-effort; never break the commit.
            pass
    rc, out, err = _git(["commit", "-q", "-m", msg], workspace)
    if rc != 0:
        logger.warning("git commit failed for round %d: %s", round_no, err)
        return None
    rc, sha, _ = _git(["rev-parse", "HEAD"], workspace)
    if rc != 0:
        return None
    return sha.strip()

def list_checkpoints(workspace: Path, limit: int = 50) -> list[dict]:
    """Return all kairos checkpoints (newest first), parsed from commit messages.

    Each entry: {sha, round, score, approved, summary, ts}.
    """
    workspace = Path(workspace)
    if not ensure_repo(workspace):
        return []
    fmt = "%H%x1f%ct%x1f%s%x1f%b"
    rc, out, _ = _git(["log", f"--max-count={limit}", f"--format={fmt}",
                       "--grep=^kairos:"], workspace)
    if rc != 0 or not out.strip():
        return []
    cps = []
    for line in out.strip().splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) < 4:
            continue
        sha, ts, subject, body = parts
        # subject = "kairos: round N verdict (score X)"
        try:
            head = subject.split(":", 1)[1].strip()  # "round N ... (score X)"
            tokens = head.split()
            round_no = int(tokens[1])
            score = int(head.split("score ", 1)[1].split(")")[0])
            approved = "approved" in head
        except (IndexError, ValueError):
            continue
        cps.append({
            "sha": sha,
            "round": round_no,
            "score": score,
            "approved": approved,
            "summary": body.strip(),
            "ts": float(ts),
        })
    return cps

def checkout_checkpoint(workspace: Path, sha: str) -> tuple[bool, str]:
    """Restore the working tree to a checkpoint SHA.

    Uses `git read-tree -u --reset` so uncommitted changes are wiped but
    the commit graph stays intact (no destructive history rewrite). The
    user can `git checkout HEAD` later to return to the tip.
    """
    workspace = Path(workspace)
    if not ensure_repo(workspace):
        return False, "not a git repo"
    rc, _, err = _git(["read-tree", "-u", "--reset", sha], workspace)
    if rc != 0:
        return False, err or "checkout failed"
    return True, ""

class CheckpointTool(BaseTool):
    """Manual checkpoint tool for the Coder.

    The orchestrator auto-checkpoints every round, but the Coder can
    call this explicitly when it wants a labeled checkpoint mid-round
    (e.g. "before risky refactor", "after test pass").
    """

    name = "checkpoint"
    description = (
        "Save the current state as a labeled git checkpoint. Use this "
        "before risky changes so you can roll back via the UI if the "
        "next round's review fails."
    )

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string",
                              "description": "Short human-readable label (e.g. 'before refactor')"},
                },
                "required": ["label"],
            },
        }

    async def execute(self, label: str = "", **kwargs) -> ToolResult:
        if not label:
            return ToolResult(success=False, output="", error="label is required")
        # The Coder doesn't know its workspace directly; the orchestrator
        # wires `workspace_path` onto the tool when it constructs the
        # agent. Fall back to the Coder's message_bus metadata if not set.
        workspace = getattr(self, "_workspace_path", None)
        if workspace is None:
            return ToolResult(success=False, output="",
                              error="CheckpointTool not wired to a workspace")
        sha = checkpoint_round(Path(workspace), round_no=0, score=0,
                                summary=label, approved=False)
        if sha is None:
            return ToolResult(success=False, output="",
                              error="nothing to commit (no changes since last checkpoint)")
        return ToolResult(success=True, output=f"checkpoint {sha[:8]} saved",
                          metadata={"sha": sha, "label": label})
def revert_to_last(workspace: Path, round_no: int) -> bool:
    """Revert the workspace to the checkpoint commit before round_no.

    Returns True on success. Used by the regression detector in
    loop_runner: when the new round's score dropped sharply from the
    previous best, roll the workspace back so the next Coder attempt
    starts from the last known-good state instead of piling more
    changes on top of a regression.

    Uses `git reset --hard <sha>` to a checkpoint commit strictly
    before the failing round, then leaves the workspace clean.
    """
    try:
        workspace = Path(workspace)
        if not ensure_repo(workspace):
            return False
        checkpoints = list_checkpoints(workspace, limit=200)
        earlier = [cp for cp in checkpoints if cp["round"] < round_no]
        if not earlier:
            return False
        target = max(earlier, key=lambda cp: cp["round"])
        sha = target["sha"]
        rc, _, err = _git(["reset", "--hard", sha], workspace)
        if rc != 0:
            logger.warning("git reset --hard %s failed: %s", sha, err)
            return False
        return True
    except Exception:
        logger.debug("revert_to_last failed (non-fatal)", exc_info=True)
        return False

def revert_file(workspace: Path, sha: str, path: str) -> tuple[bool, str]:
    """Restore a single file to its state at a specific checkpoint SHA.

    Uses `git checkout <sha> -- <path>` so the rest of the working tree
    is untouched. Returns (ok, error_message). Best-effort: never raises.
    """
    workspace = Path(workspace)
    if not ensure_repo(workspace):
        return False, "not a git repo"
    rc, _, err = _git(["checkout", sha, "--", path], workspace)
    if rc != 0:
        return False, err or "checkout failed"
    return True, ""