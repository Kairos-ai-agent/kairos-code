"""Project API routes — LoopReview mode."""

from __future__ import annotations

import base64

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api.deps import orchestrator
from kairos.loop.review_loop import _loop_health_score

router = APIRouter()


@router.get("")
async def list_projects():
    projects = orchestrator.list_projects()
    return {"projects": [p.to_dict() for p in projects]}


@router.post("")
async def create_project(request: "CreateProjectRequest"):
    project = orchestrator.create_project(request.name, request.description, request.work_dir)
    return project.to_dict()


@router.get("/{project_id}")
async def get_project(project_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return project.to_dict()


@router.post("/{project_id}/start")
async def start_loop(project_id: str, request: "StartLoopRequest"):
    """Start the Coder ↔ Reviewer loop. Returns immediately; the loop
    runs in the background and emits progress over the WebSocket."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if project.loop_task and not project.loop_task.done():
        raise HTTPException(status_code=409, detail="A loop is already running for this project")
    try:
        session_id = await orchestrator.start_loop(project_id, request.requirement)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "started", "project_id": project_id, "session_id": session_id}


@router.post("/{project_id}/stop")
async def stop_loop(project_id: str):
    """User-initiated stop of the loop."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    stopped = orchestrator.stop_loop(project_id)
    return {"status": "stopping" if stopped else "no_loop_running",
            "project_id": project_id}


@router.get("/{project_id}/plan")
async def get_plan(project_id: str):
    """Get the Coder'"'"'s draft plan waiting for user approval (if any)."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    plan = orchestrator.get_plan(project_id)
    if plan is None:
        return {"pending": False, "text": "", "decision": None, "round": 0}
    return plan


@router.post("/{project_id}/plan/approve")
async def approve_plan(project_id: str):
    """User approves the Coder'"'"'s plan — loop continues with tool execution."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok = orchestrator.approve_plan(project_id)
    return {"status": "approved" if ok else "no_plan_pending",
            "project_id": project_id}


@router.post("/{project_id}/plan/reject")
async def reject_plan(project_id: str):
    """User rejects the plan — loop stops."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok = orchestrator.reject_plan(project_id)
    return {"status": "rejected" if ok else "no_plan_pending",
            "project_id": project_id}


@router.get("/{project_id}/loop")
async def get_loop_state(project_id: str):
    """Inspect current loop state: round, last score, last issues, etc."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    session = project.loop_session
    if not session:
        return {"running": False, "round": 0}
    return {
        "running": bool(project.loop_task and not project.loop_task.done()),
        "session_id": session.session_id,
        "round": session.round,
        "last_score": session.last_score,
        "last_approve": session.last_approve,
        "no_progress_count": session.no_progress_count,
        "history": session.history[-5:],
        "user_stopped": session.user_stopped,
    }


@router.get("/{project_id}/health")
async def get_loop_health(project_id: str):
    """Real-time loop health score (0-100). 100 = green, <=20 = critical.

    Combines score trend, infra-failure streak, and no-progress counter
    into one number so the UI can show a color-coded badge without
    re-deriving the formula on the frontend.
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    session = project.loop_session
    if not session:
        return {"health": 100, "running": False, "round": 0,
                "factors": {"score_trend": [], "infra_streak": 0, "no_progress": 0}}
    health = _loop_health_score(session)
    return {
        "health": health,
        "running": bool(project.loop_task and not project.loop_task.done()),
        "round": session.round,
        "factors": {
            "score_trend": list(getattr(session, "score_window", []) or []),
            "infra_streak": session.infra_failure_streak,
            "no_progress": session.no_progress_count,
        },
    }


class BestOfNRequest(BaseModel):
    best_of_n: int = 1


@router.post("/{project_id}/loop/best_of_n")
async def set_best_of_n(project_id: str, request: "BestOfNRequest"):
    """Configure best-of-N for the current / next loop on this project.

    best_of_n=1 is the default (single Coder attempt per round). Setting
    this BEFORE start_loop will pass best_of_n to the LoopSession. Setting
    it AFTER start_loop updates session.best_of_n in place so the next
    round uses the new value.
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if request.best_of_n < 1 or request.best_of_n > 5:
        raise HTTPException(status_code=400, detail="best_of_n must be in [1, 5]")
    project.best_of_n = request.best_of_n
    if project.loop_session is not None:
        project.loop_session.best_of_n = request.best_of_n
    return {"status": "ok", "project_id": project_id, "best_of_n": request.best_of_n}


@router.get("/{project_id}/loop/best_of_n")
async def get_best_of_n(project_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {"best_of_n": getattr(project, "best_of_n", 1) or 1}


# Global message stream — see #27 / #28 for why this is at /api/messages
# (and not under /projects) — FastAPI'"'"'s dynamic-segment matching shadows
# literal paths under the same prefix.
@router.get("/messages")
async def get_global_messages(limit: int = 100):
    return {"messages": orchestrator.get_message_history(limit=limit)}


@router.get("/{project_id}/messages")
async def get_project_messages(project_id: str, limit: int = 50):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    messages = orchestrator.get_message_history(limit=limit, project_id=project_id)
    return {"messages": messages}


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    orchestrator.delete_project(project_id)
    return {"status": "ok", "message": f"Project {project_id} deleted"}




# ============================================================================
# Reference files
# ============================================================================
#
# Each project can carry user-uploaded reference material (PDF, markdown,
# text, ...). The Coder gets a digest of these in its first-round prompt
# and can read the full text via `file_read` (the file is also stored
# under the project workspace so terminal tools can see it).


@router.get("/{project_id}/files")
async def list_reference_files(project_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    files = orchestrator.list_reference_files(project_id)
    return {"files": files}


@router.get("/{project_id}/files/{file_id}")
async def get_reference_file(project_id: str, file_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    record = orchestrator.get_reference_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    if record.get("project_id") != project_id:
        raise HTTPException(status_code=403, detail="File does not belong to this project")
    return record


@router.post("/{project_id}/files")
async def upload_reference_file(project_id: str,
                                  name: str = Form(...),
                                  mime: str = Form("text/plain"),
                                  file: UploadFile = File(...)):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    content = await file.read()
    file_id = orchestrator.add_reference_file(project_id, name, mime, content)
    return {"file_id": file_id, "name": name, "size": len(content)}


@router.delete("/{project_id}/files/{file_id}")
async def delete_reference_file(project_id: str, file_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    record = orchestrator.get_reference_file(file_id)
    if record is None or record.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    ok = orchestrator.delete_reference_file(file_id)
    return {"status": "deleted" if ok else "not_found", "file_id": file_id}


# ============================================================================
# Per-file revert (uses git SHA captured at auto-checkpoint)
# ============================================================================


@router.post("/{project_id}/checkpoint/revert_file")
async def revert_file(project_id: str, request: "RevertFileRequest"):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok, err = orchestrator.revert_file(project_id, request.sha, request.path)
    if not ok:
        raise HTTPException(status_code=400, detail=err or "revert failed")
    return {"status": "reverted", "project_id": project_id,
            "path": request.path, "sha": request.sha}
class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    work_dir: str = ""


class StartLoopRequest(BaseModel):
    requirement: str


class RevertFileRequest(BaseModel):
    sha: str
    path: str
