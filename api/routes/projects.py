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
    """Get the Coder's draft plan waiting for user approval (if any)."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    plan = orchestrator.get_plan(project_id)
    if plan is None:
        return {"pending": False, "text": "", "decision": None, "round": 0}
    return plan


@router.post("/{project_id}/plan/approve")
async def approve_plan(project_id: str):
    """User approves the Coder's plan — loop continues with tool execution."""
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
        "history": session.history[-5:],  # last 5 rounds
        "user_stopped": session.user_stopped,
    }


# Global message stream — see #27 / #28 for why this is at /api/messages
# (and not under /projects) — FastAPI's dynamic-segment matching shadows
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
# text, …). The Coder gets a digest of these in its first-round prompt and
# can read the full text via `file_read` (the file is also stored under
# the project's workspace for the agent's filesystem tools).
#
# Upload format: multipart/form-data with a single "file" part. The file
# is read fully into memory (we don't try to stream into SQLite) and
# stored verbatim — the Coder will see the bytes as a string. Binary
# files (PDF, images) are base64-encoded so JSON endpoints round-trip
# safely.

# Cap at 5 MB per file — anything larger is almost certainly a mistake
# (e.g. user uploaded a video). 5 MB of plain text is ~1M tokens, far
# more than we'd ever want in one Coder prompt anyway.
_MAX_FILE_SIZE = 5 * 1024 * 1024


@router.get("/{project_id}/files")
async def list_project_files(project_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {"files": orchestrator.list_reference_files(project_id)}


@router.post("/{project_id}/files")
async def upload_project_file(project_id: str, file: UploadFile = File(...)):
    """Upload a reference file to a project. multipart/form-data."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    raw = await file.read()
    if len(raw) > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(raw)} bytes; max {_MAX_FILE_SIZE})",
        )
    # Best-effort text decode; fall back to base64 for binary so the
    # Coder can at least see SOMETHING (and file_read will load the
    # original bytes for it later).
    mime = file.content_type or "application/octet-stream"
    name = file.filename or "unnamed"
    if mime.startswith("text/") or mime.endswith("+json") or mime.endswith("+xml") \
            or mime in ("application/json", "application/xml", "application/yaml"):
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = base64.b64encode(raw).decode("ascii")
            mime = "application/base64"
    else:
        content = base64.b64encode(raw).decode("ascii")
        mime = "application/base64"
    record = orchestrator.add_reference_file(
        project_id=project_id,
        name=name,
        mime=mime,
        size=len(raw),
        content=content,
    )
    return {"status": "ok", "file": _file_meta(record)}


@router.delete("/{project_id}/files/{file_id}")
async def delete_project_file(project_id: str, file_id: str):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok = orchestrator.delete_reference_file(file_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    return {"status": "ok"}


def _file_meta(record: dict) -> dict:
    """Project a DB row to the metadata-only shape the UI sees."""
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "mime": record.get("mime"),
        "size": record.get("size"),
        "uploaded_at": record.get("uploaded_at"),
    }


# ============================================================================
# Stats, diff, checkpoint
# ============================================================================
#
# Stats: per-project cost + score history + gate firings. Read-only,
# powers the dashboard charts.
#
# Diff: between any two rounds. Built from git history (auto-checkpointed
# at review_loop.py:_auto_checkpoint). Falls back to "git not available"
# if the project never had git.
#
# Checkpoint: list all rounds + checkout a round (destructive; the UI
# should confirm).

from pathlib import Path

from kairos.tools.checkpoint import list_checkpoints, checkout_checkpoint, revert_file


@router.get("/{project_id}/stats")
async def get_project_stats(project_id: str):
    """Per-project cost + score trend + gate-firing counters.

    Drives the dashboard charts. Returns empty lists when no loop has
    run yet (project exists but is dormant).
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    session = project.loop_session
    if not session:
        return {
            "running": False,
            "rounds": [],
            "total_tokens_used": 0,
            "approximate_cost_usd": 0.0,
            "infra_failure_streak": 0,
            "no_progress_count": 0,
            "stops": [],
        }
    rounds = []
    for entry in session.history:
        review = entry.get("review") or {}
        rounds.append({
            "round": entry.get("round"),
            "score": review.get("score", 0),
            "approve": review.get("approve", False),
            "issues": len(review.get("issues") or []),
            "summary": (review.get("summary") or "")[:120],
            "ts": entry.get("ts", 0),
        })
    # Rough cost: ~$0.005 per 1k tokens (blended input+output average).
    # Real cost varies by provider; the UI should label this as estimate.
    approx_cost = round(session.total_tokens_used / 1000 * 0.005, 4)
    return {
        "running": bool(project.loop_task and not project.loop_task.done()),
        "rounds": rounds,
        "score_window": list(session.score_window),
        "total_tokens_used": session.total_tokens_used,
        "approximate_cost_usd": approx_cost,
        "infra_failure_streak": session.infra_failure_streak,
        "no_progress_count": session.no_progress_count,
        "loop_health": _loop_health_score(session),
    }


@router.get("/{project_id}/preferences")
async def list_project_preferences(project_id: str):
    """List all style/rule preferences saved for this project.

    Returned as a list of {id, project_id, kind, rule, created_at}.
    The Coder loop reads these and prepends them to the system prompt
    so the user'"'"'s conventions are followed every round.
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return orchestrator.list_preferences(project_id)


@router.post("/{project_id}/preferences")
async def add_project_preference(project_id: str, body: dict):
    """Add a new style/rule preference for this project.

    Body: {"kind": "always"|"never"|"prefer", "rule": "..."}.
    The Coder system prompt is augmented with this rule on the NEXT
    round of the loop (already-running loops keep their current
    system prompt; restart the loop to apply).
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    kind = body.get("kind", "always")
    rule = (body.get("rule") or "").strip()
    if not rule:
        raise HTTPException(status_code=400, detail="rule cannot be empty")
    if len(rule) > 500:
        raise HTTPException(status_code=400, detail="rule too long (max 500 chars)")
    try:
        pref_id = orchestrator.add_preference(project_id, kind, rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": pref_id, "kind": kind, "rule": rule}


@router.delete("/{project_id}/preferences/{pref_id}")
async def delete_project_preference(project_id: str, pref_id: int):
    """Delete a style/rule preference. 404 if the id is not in this project."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if not orchestrator.delete_preference(project_id, pref_id):
        raise HTTPException(status_code=404, detail=f"Preference not found: {pref_id}")
    return {"ok": True}


@router.get("/{project_id}/diff")
async def get_round_diff(project_id: str, from_round: int = 0, to_round: int = 0):
    """Diff between two rounds. Both default to 0 (== before any round).

    Uses git: from_round's checkpoint SHA vs to_round's checkpoint SHA.
    If git isn't available, returns an empty diff (caller should treat
    as "no checkpoint history").
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    workspace = Path(getattr(project, "work_dir", None) or project.workspace)
    cps = list_checkpoints(workspace)
    if not cps:
        return {"from_round": from_round, "to_round": to_round,
                "files": [], "patch": "", "available": False}
    by_round = {cp["round"]: cp for cp in cps}
    from_sha = by_round.get(from_round, {}).get("sha") if from_round else None
    to_sha = by_round.get(to_round, {}).get("sha") if to_round else None
    if not to_sha:
        return {"from_round": from_round, "to_round": to_round,
                "files": [], "patch": "", "available": False}
    import subprocess
    range_arg = (from_sha or f"{to_sha}^") if from_sha else to_sha
    try:
        proc = subprocess.run(
            ["git", "diff", "--stat", "--patch", range_arg, to_sha],
            cwd=str(workspace), capture_output=True, text=True, timeout=30,
            check=False,
        )
        patch = proc.stdout
    except Exception:
        patch = ""
    # Parse --numstat lines: "<add>\t<del>\t<path>"
    files = []
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            continue
        if "\t" in line:
            parts = line.split("\t", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                files.append({"path": parts[2],
                              "added": int(parts[0]),
                              "removed": int(parts[1])})
    return {
        "from_round": from_round,
        "to_round": to_round,
        "from_sha": from_sha,
        "to_sha": to_sha,
        "files": files,
        "patch": patch[:200_000],  # cap at 200KB to keep WS happy
        "available": True,
    }


class CheckoutRequest(BaseModel):
    sha: str


class AnswerAskRequest(BaseModel):
    answer: str


@router.get("/{project_id}/plan/visualization")
async def get_plan_visualization(project_id: str):
    """Mermaid flowchart + file tree for the current plan, if any."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    viz = orchestrator.get_plan_visualization(project_id)
    if not viz:
        return {"mermaid": "", "file_tree": "", "round": 0}
    return viz


@router.get("/{project_id}/cache")
async def get_tool_cache_stats(project_id: str):
    """Per-round tool cache effectiveness.

    Returns hits/misses/hit_rate for the current round.
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return orchestrator.get_cache_stats(project_id)


@router.get("/{project_id}/comments")
async def get_round_comments(project_id: str, round_no: int = 0):
    """Inline ::code-comment JSON for a round's Reviewer verdict.
    round_no=0 means latest round."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    payload = orchestrator.get_round_comments(project_id, round_no)
    if not payload:
        return {"round": round_no, "comments": [], "jsonl": ""}
    return payload


@router.get("/{project_id}/ask")
async def get_pending_ask(project_id: str):
    """Return the pending Reviewer ask_human payload, if any."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    payload = orchestrator.get_pending_ask(project_id)
    if not payload:
        return {"pending": False, "question": "", "context": "", "round": 0}
    return payload


@router.post("/{project_id}/ask/answer")
async def answer_pending_ask(project_id: str, request: AnswerAskRequest):
    """User answers the Reviewer's pending question. Releases the gate."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok = orchestrator.answer_ask(project_id, request.answer)
    return {"status": "answered" if ok else "no_pending_ask", "project_id": project_id}


@router.post("/{project_id}/checkpoint")
async def checkout_project_checkpoint(project_id: str, request: CheckoutRequest):
    """Restore working tree to a checkpoint SHA. Destructive — uncommitted
    changes are wiped. UI should confirm."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    workspace = Path(getattr(project, "work_dir", None) or project.workspace)
    ok, err = checkout_checkpoint(workspace, request.sha)
    if not ok:
        raise HTTPException(status_code=500, detail=err or "checkout failed")
    return {"status": "checked_out", "sha": request.sha}



class RevertFileRequest(BaseModel):
    sha: str
    path: str


@router.post("/{project_id}/checkpoint/revert_file")
async def revert_project_file(project_id: str, request: RevertFileRequest):
    """Restore one file to its state at the given checkpoint SHA.

    Safe: only the listed path is touched; the rest of the working tree
    is preserved. UI should still confirm; this is destructive for the
    named file (uncommitted changes to that file are lost).
    """
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok, err = orchestrator.revert_file(project_id, request.sha, request.path)
    if not ok:
        raise HTTPException(status_code=500, detail=err or "revert failed")
    return {"status": "reverted", "sha": request.sha, "path": request.path}

@router.get("/{project_id}/checkpoint")
async def list_project_checkpoints(project_id: str):
    """List all auto-checkpoints for this project (newest first)."""
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    workspace = Path(getattr(project, "work_dir", None) or project.workspace)
    cps = list_checkpoints(workspace)
    return {"checkpoints": cps}


# --- request bodies ---

class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    work_dir: str = ""


class StartLoopRequest(BaseModel):
    requirement: str


class SaveRequirementsRequest(BaseModel):
    requirements: str


@router.post("/{project_id}/requirements")
async def save_requirements(project_id: str, request: SaveRequirementsRequest):
    project = orchestrator.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    orchestrator.save_requirements(project_id, request.requirements)
    return {"status": "ok"}

    def revert_file(self, project_id: str, sha: str, path: str) -> tuple[bool, str]:
        """Restore a single file to its state at the given checkpoint SHA."""
        project = self._projects.get(project_id)
        if not project:
            return False, f"Project not found: {project_id}"
        workspace = Path(getattr(project, "work_dir", None) or project.workspace)
        return revert_file(workspace, sha, path)