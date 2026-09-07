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
    # SECURITY: the requested path must be inside a known project workspace.
    # There is intentionally NO "no projects => allow anything" escape hatch
    # (it was a path-traversal / arbitrary-file-read hole). Empty or
    # non-dot-prefixed file_extensions are also rejected so a caller can't
    # turn `rglob("*.ext")` into `rglob("*")`.
    if any(not ext or not ext.startswith(".") for ext in request.file_extensions):
        raise HTTPException(
            status_code=400,
            detail="file_extensions must be non-empty dot-prefixed extensions (e.g. '.py')",
        )

    project_path = Path(request.project_path)

    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {request.project_path}")

    known_projects = [Path(p.workspace) for p in orchestrator.list_projects()]
    is_known = any(
        project_path.resolve().is_relative_to(wp.resolve())
        for wp in known_projects
    )
    if not is_known:
        raise HTTPException(status_code=403, detail="Path not in known project directories")

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
