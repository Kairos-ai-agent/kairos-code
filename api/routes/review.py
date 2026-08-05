"""Review API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import get_review_engine, orchestrator

router = APIRouter()


class ReviewProjectRequest(BaseModel):
    project_path: str
    file_extensions: list[str] = [".py", ".js", ".ts", ".tsx", ".jsx"]


class ReviewFileRequest(BaseModel):
    file_path: str
    code: str


@router.post("/project")
async def review_project(request: ReviewProjectRequest):
    """Review an entire project."""
    project_path = Path(request.project_path)

    # Validate path is within known project workspaces
    known_projects = [Path(p.workspace) for p in orchestrator.list_projects()]
    is_known = any(
        project_path.resolve().is_relative_to(wp.resolve())
        for wp in known_projects
    ) if known_projects else True  # Allow if no projects (first run)

    if not is_known:
        raise HTTPException(status_code=403, detail="Path not in known project directories")

    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {request.project_path}")

    engine = get_review_engine()
    try:
        report = await engine.review_project(
            str(project_path),
            file_extensions=request.file_extensions,
        )
        return report.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/file")
async def review_file(request: ReviewFileRequest):
    """Review a single file."""
    engine = get_review_engine()
    try:
        review = await engine.review_file(request.file_path, request.code)
        return review.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
