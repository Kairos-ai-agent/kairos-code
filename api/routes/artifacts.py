"""Artifacts API — what a run produced, and the comments on it.

The reading half of `kairos/artifacts.py`. Two shapes, deliberately:

* `GET /api/projects/{id}/artifacts` — everything this project has produced,
  newest first, optionally filtered by `kind`. This is the answer to "what came
  out of that run?", which until now meant scrolling a transcript.
* `GET|POST /api/artifacts/{id}/comments` — the thread. An artifact nobody can
  comment on is a log line with a nicer name.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])


class CommentBody(BaseModel):
    body: str
    author: str = "user"


@router.get("/projects/{project_id}/artifacts")
async def list_artifacts(project_id: str, kind: str = "", limit: int = 50):
    from kairos import artifacts

    rows = artifacts.list_for_project(project_id, kind=kind, limit=limit)
    return {"project_id": project_id, "kind": kind, "count": len(rows),
            "artifacts": rows}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    from kairos import artifacts

    found = artifacts.get(artifact_id)
    if not found:
        raise HTTPException(404, "no artifact with that id")
    return found


@router.get("/artifacts/{artifact_id}/file")
async def artifact_file(artifact_id: str):
    """Serve the file an artifact points at — a screenshot, in practice.

    Containment is the whole point of this route. The path comes from a database
    row, and a row is not a promise: only files **under the app's data
    directory** (the directory the project database lives in) are served, after
    resolution, and only if they are images. Everything else is refused rather
    than streamed.
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    from kairos import artifacts

    row = artifacts.get(artifact_id)
    if not row or not row.get("path"):
        raise HTTPException(404, "that artifact has no file")

    store = artifacts._db()
    db_path = getattr(store, "db_path", None)
    if not db_path:
        raise HTTPException(503, "no data directory is open in this process")
    root = Path(db_path).resolve().parent

    target = Path(row["path"]).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(403, "that file is outside the app's data directory")
    if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(415, "only images are served here")
    if not target.is_file():
        raise HTTPException(404, "the file is gone")
    return FileResponse(str(target))


@router.get("/artifacts/{artifact_id}/comments")
async def list_comments(artifact_id: str, limit: int = 100):
    from kairos import artifacts

    if not artifacts.get(artifact_id):
        raise HTTPException(404, "no artifact with that id")
    rows = artifacts.comments(artifact_id, limit=limit)
    return {"artifact_id": artifact_id, "count": len(rows), "comments": rows}


@router.post("/artifacts/{artifact_id}/comments")
async def add_comment(artifact_id: str, body: CommentBody):
    from kairos import artifacts

    if not artifacts.get(artifact_id):
        raise HTTPException(404, "no artifact with that id")
    try:
        saved = artifacts.add_comment(artifact_id, body.body, author=body.author)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if saved is None:
        raise HTTPException(503, "comments are not available in this process")
    return saved
