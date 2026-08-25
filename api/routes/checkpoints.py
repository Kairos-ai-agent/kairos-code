"""Checkpoint HTTP API.

Exposes the existing `kairos.tools.checkpoint` helpers over HTTP so
the web UI can list / restore / diff checkpoints.

Endpoints
---------
  GET  /api/projects/{project_id}/checkpoints
       → JSON list, newest first
  POST /api/projects/{project_id}/checkpoints/restore
       body: {"sha": "<full-or-short-sha>"}
       → restores the working tree to that checkpoint
  GET  /api/projects/{project_id}/checkpoints/{sha}/diff
       → unified diff vs current working tree
  POST /api/projects/{project_id}/checkpoints
       body: {"label": "before risky change", "round": 3}
       → explicit checkpoint; round defaults to 0

The work_dir is taken from the project record. Routes return 404
if the project is unknown and 400 if `work_dir` is missing /
not a git repo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import orchestrator
from kairos.tools.checkpoint import (
    checkpoint_round,
    checkout_checkpoint,
    list_checkpoints,
)

router = APIRouter()


def _resolve_project(project_id: str):
    """Look up the project and return its Path (work_dir or workspace).

    Raises HTTPException(404) if not found.
    """
    project = orchestrator._projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    work_dir = getattr(project, "work_dir", None) or str(project.workspace)
    if not work_dir:
        raise HTTPException(status_code=400, detail="project has no work_dir")
    return Path(work_dir)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RestoreRequest(BaseModel):
    sha: str


class CreateCheckpointRequest(BaseModel):
    label: str = ""
    round: int = 0
    score: int = 0
    summary: str = ""
    approved: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/checkpoints")
def list_project_checkpoints(project_id: str, limit: int = 50) -> Dict[str, Any]:
    """Return all kairos-tagged checkpoints, newest first."""
    work_dir = _resolve_project(project_id)
    items = list_checkpoints(work_dir, limit=limit)
    return {"project_id": project_id, "checkpoints": items, "count": len(items)}


@router.post("/projects/{project_id}/checkpoints/restore")
def restore_checkpoint(project_id: str, req: RestoreRequest) -> Dict[str, Any]:
    """Reset the working tree to a checkpoint."""
    if not req.sha or not req.sha.strip():
        raise HTTPException(status_code=400, detail="sha is required")
    work_dir = _resolve_project(project_id)
    ok, err = checkout_checkpoint(work_dir, req.sha.strip())
    if not ok:
        raise HTTPException(status_code=400, detail=err or "checkout failed")
    return {
        "project_id": project_id,
        "restored_to": req.sha.strip(),
        "ok": True,
    }


@router.get("/projects/{project_id}/checkpoints/{sha}/diff")
def diff_checkpoint(project_id: str, sha: str) -> Dict[str, Any]:
    """Return a unified diff between `sha` and the current working tree."""
    work_dir = _resolve_project(project_id)
    try:
        proc = subprocess.run(
            ["git", "diff", sha],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="git not installed")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="git diff timed out")
    if proc.returncode not in (0, 1):  # 1 means there are differences
        raise HTTPException(
            status_code=500, detail=proc.stderr.strip() or "git diff failed"
        )
    return {
        "project_id": project_id,
        "sha": sha,
        "diff": proc.stdout,
    }


@router.post("/projects/{project_id}/checkpoints")
def create_checkpoint(
    project_id: str, req: CreateCheckpointRequest
) -> Dict[str, Any]:
    """Force a checkpoint now (the orchestrator does this
    automatically each round; this endpoint is for ad-hoc labels)."""
    work_dir = _resolve_project(project_id)
    if not req.label and not req.summary:
        raise HTTPException(
            status_code=400,
            detail="either label or summary is required",
        )
    summary_text = req.summary or req.label
    sha = checkpoint_round(
        work_dir,
        round_no=req.round,
        score=req.score,
        summary=summary_text,
        approved=req.approved,
    )
    if sha is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "checkpoint not created (no changes to commit, or "
                "git operation failed)"
            ),
        )
    return {
        "project_id": project_id,
        "sha": sha,
        "label": req.label,
    }
