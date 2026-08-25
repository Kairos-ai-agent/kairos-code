"""HTTP API for the Agent Teams feature.

Endpoints (mounted under /api/projects/{project_id}/team by app.py):

    POST   /team/create        — build a team from a list of task descriptions
    POST   /team/dispatch      — run the team's pending tasks in parallel
    GET    /team/status        — return the current task board + counts
    POST   /team/merge         — merge worker branches back into main
    DELETE /team               — tear down the team (cleanup worktrees)

Design:
* We keep a `dict[project_id, Team]` in module-scope memory so HTTP
  callers can hit the same Team across multiple requests (create →
  dispatch → status → merge is a 4-request flow).
* The team object is process-local. If Kairos is restarted, the team
  is lost. The SharedTaskBoard has a `persist_board_path` so the
  *board state* survives a restart even if the live team doesn't.
* For non-trivial parallelism the worker_fn is the project's Coder
  instance (a single Coder is reused across all workers; each worker
  gets a fresh conversation memory via the AgentTask API).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from kairos.teams import (
    MergeStrategy,
    SharedTaskBoard,
    Team,
    TeamConfig,
    TeamTask,
    WorkerResult,
    run_team,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# In-process team registry. We don't bother persisting the Team object
# itself — the SharedTaskBoard can be reloaded from disk if needed.
_TEAMS: Dict[str, Dict[str, Team]] = {}  # project_id -> team_id -> Team


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class CreateTeamRequest(BaseModel):
    descriptions: List[str] = Field(..., min_length=1)
    max_workers: int = 3
    merge_strategy: str = "fast_forward"  # fast_forward | squash | manual
    worker_role: str = "coder"
    timeout_seconds: int = 600
    persist_board: bool = True


class CreateTeamResponse(BaseModel):
    team_id: str
    project_id: str
    task_count: int
    counts: Dict[str, int]


class DispatchResponse(BaseModel):
    team_id: str
    completed: int
    succeeded: int
    failed: int
    results: List[dict]


class StatusResponse(BaseModel):
    team_id: str
    project_id: str
    counts: Dict[str, int]
    board: List[dict]
    results: List[dict] = []


class MergeRequest(BaseModel):
    strategy: Optional[str] = None  # override config strategy


class MergeResponse(BaseModel):
    team_id: str
    strategy: str
    merged: List[str]
    skipped: List[str]
    failed: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_team(project_id: str, team_id: str) -> Team:
    bucket = _TEAMS.get(project_id, {})
    team = bucket.get(team_id)
    if team is None:
        raise HTTPException(status_code=404,
                            detail=f"team {team_id} not found for project {project_id}")
    return team


def _persist_path(project_id: str, team_id: str) -> Path:
    return Path(f"data/teams/{project_id}_{team_id}.json")


def _make_worker_fn(project):
    """Return an async worker_fn bound to the project's Coder agent.

    The Coder is reused (one Coder per project). Each invocation
    creates a fresh AgentTask so the conversation memory doesn't
    leak between workers.
    """
    from kairos.agents.base import AgentTask
    coder = project.coder
    if coder is None:
        raise HTTPException(status_code=500,
                            detail="project has no coder agent wired")

    async def worker_fn(task: TeamTask, ctx) -> str:
        agent_task = AgentTask(
            id=f"{ctx.team_id}.{task.id}",
            title=task.title,
            description=task.description,
        )
        return await coder.run(agent_task)

    return worker_fn


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/{project_id}/team/create", response_model=CreateTeamResponse)
async def create_team(project_id: str, request: CreateTeamRequest):
    from api.deps import orchestrator  # local to avoid import-time cycle
    project = orchestrator.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
    work_dir = project.work_dir or str(project.workspace)
    if not work_dir:
        raise HTTPException(status_code=400, detail="project has no work_dir")

    # Validate merge_strategy early so we don't surface enum errors later.
    try:
        strategy = MergeStrategy(request.merge_strategy)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail=f"unknown merge_strategy: {request.merge_strategy}")

    team = Team.from_descriptions(
        descriptions=request.descriptions,
        project_id=project_id,
        work_dir=work_dir,
        worker_fn=_make_worker_fn(project),
        config=TeamConfig(
            max_workers=max(1, request.max_workers),
            merge_strategy=strategy,
            worker_role=request.worker_role,
            timeout_seconds=max(1, request.timeout_seconds),
            persist_board_path=(
                str(_persist_path(project_id, "pending"))
                if request.persist_board else None
            ),
        ),
    )
    _TEAMS.setdefault(project_id, {})[team.team_id] = team
    if request.persist_board:
        team.board.save(_persist_path(project_id, team.team_id))
    return CreateTeamResponse(
        team_id=team.team_id,
        project_id=project_id,
        task_count=len(request.descriptions),
        counts=team.status_counts(),
    )


@router.post("/{project_id}/team/{team_id}/dispatch", response_model=DispatchResponse)
async def dispatch_team(project_id: str, team_id: str):
    team = _get_team(project_id, team_id)
    try:
        results: List[WorkerResult] = await team.dispatch()
    except Exception as e:
        logger.exception("team dispatch failed")
        raise HTTPException(status_code=500, detail=f"dispatch failed: {e}")
    completed = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    return DispatchResponse(
        team_id=team_id,
        completed=len(results),
        succeeded=completed,
        failed=failed,
        results=[
            {
                "task_id": r.task_id,
                "worker_id": r.worker_id,
                "success": r.success,
                "output": r.output,
                "duration_seconds": r.duration_seconds,
            }
            for r in results
        ],
    )


@router.get("/{project_id}/team/{team_id}/status", response_model=StatusResponse)
async def team_status(project_id: str, team_id: str):
    team = _get_team(project_id, team_id)
    return StatusResponse(
        team_id=team_id,
        project_id=project_id,
        counts=team.status_counts(),
        board=team.board.snapshot(),
        results=[
            {
                "task_id": r.task_id,
                "worker_id": r.worker_id,
                "success": r.success,
                "output": r.output,
                "duration_seconds": r.duration_seconds,
            }
            for r in team.results()
        ],
    )


@router.post("/{project_id}/team/{team_id}/merge", response_model=MergeResponse)
async def merge_team(project_id: str, team_id: str, request: MergeRequest = MergeRequest()):
    team = _get_team(project_id, team_id)
    if request.strategy:
        try:
            team.config.merge_strategy = MergeStrategy(request.strategy)
        except ValueError:
            raise HTTPException(status_code=400,
                                detail=f"unknown merge_strategy: {request.strategy}")
    from kairos.worktree import WorktreeManager
    wm = WorktreeManager(repo_path=Path(team.work_dir))
    result = team.merge(worktree_manager=wm)
    return MergeResponse(
        team_id=team_id,
        strategy=result.strategy.value,
        merged=result.merged,
        skipped=result.skipped,
        failed=result.failed,
    )


@router.delete("/{project_id}/team/{team_id}")
async def delete_team(project_id: str, team_id: str):
    team = _get_team(project_id, team_id)
    # Best-effort: clean up the persisted board file.
    persist_path = _persist_path(project_id, team_id)
    if persist_path.exists():
        try:
            persist_path.unlink()
        except OSError:
            pass
    _TEAMS.get(project_id, {}).pop(team_id, None)
    return {"deleted": team_id}


# ---------------------------------------------------------------------------
# Convenience: re-export for app.py
# ---------------------------------------------------------------------------


def get_team_for_project(project_id: str, team_id: str) -> Optional[Team]:
    return _TEAMS.get(project_id, {}).get(team_id)
