"""Project API routes 鈥?LoopReview mode."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api import deps
from kairos.loop.review_loop import _loop_health_score

logger = logging.getLogger(__name__)

router = APIRouter()


def _orch():
    """Resolve the live orchestrator via the deps module.

    Routes call `orch().method(...)` so tests that monkeypatch
    `api.deps.orchestrator` are seen by every handler without needing
    to also patch a stale `from api.deps import orchestrator`
    reference (which is what bit us with test_checkpoints_api /
    test_sessions_api). New routes should prefer
    `Depends(get_orchestrator)` 鈥?this shim exists for the 37
    existing call sites we'd otherwise have to rewrite.
    """
    return deps.orchestrator


@router.get("")
async def list_projects():
    projects = _orch().list_projects()
    return {"projects": [p.to_dict() for p in projects]}


@router.post("")
async def create_project(request: "CreateProjectRequest"):
    # Validate / normalize work_dir BEFORE creating the project.
    # A folder picked in the BrowsePanel can vanish (deleted, or a
    # network / removable drive disconnected) between listing and
    # submit; give the user a clear message instead of a project
    # whose working directory 404s every workbench / fs call.
    if request.work_dir and request.work_dir.strip():
        try:
            wd = Path(request.work_dir.strip()).expanduser()
            if not wd.is_absolute():
                wd = wd.resolve()
            if not wd.exists():
                # Recreate the folder so the project is usable (the
                # orchestrator would create it anyway on agent attach).
                wd.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise HTTPException(
                status_code=400,
                detail=f"工作目录不可访问: {request.work_dir}",
            )
    project = _orch().create_project(request.name, request.description, request.work_dir)
    return project.to_dict()


# --- Literal routes for /settings and /cost MUST be registered BEFORE
# /{project_id} — otherwise Starlette matches "settings" as a project_id
# and returns 404. Keep these above the {project_id} catch-all.
@router.get("/settings")
async def get_global_settings():
    """Return the global settings (Voice / MCP / Cloud / Metrics / Ollama)."""
    from kairos.settings_store import get_store, _to_dict
    return _to_dict(get_store().get())


@router.post("/settings")
async def update_global_settings(patch: dict):
    """Merge a partial settings dict into the global settings."""
    from kairos.settings_store import get_store, _to_dict
    try:
        s = get_store().update(patch or {})
        return _to_dict(s)
    except Exception as e:
        # R38.6.4: surface the actual failure reason in the 500
        # response so the frontend can show it (instead of just
        # "[HTTP 500] Request failed with status code 500").
        import traceback
        logger.error("update_global_settings failed: %s",
                     traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {e}"[:400],
        )


@router.get("/cost")
async def get_global_cost():
    """Global cost summary across all projects (in-process records)."""
    from kairos.cost import get_tracker
    tracker = get_tracker()
    return {
        "global": tracker.summary(),
        "by_project": {pid: s.to_dict() for pid, s in tracker.by_project().items()},
    }


@router.get("/{project_id}")
async def get_project(project_id: str):
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return project.to_dict()


@router.post("/{project_id}/start")
async def start_loop(project_id: str, request: "StartLoopRequest"):
    """Start the Coder <-> Reviewer loop. Returns immediately; the loop
    runs in the background and emits progress over the WebSocket."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if project.loop_task and not project.loop_task.done():
        raise HTTPException(status_code=409, detail="A loop is already running for this project")
    try:
        session_id = await _orch().start_loop(project_id, request.requirement)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "started", "project_id": project_id, "session_id": session_id}


# ---------------------------------------------------------------------------
# Round 37: single-turn chat (no loop)
# ---------------------------------------------------------------------------


@router.post("/{project_id}/chat")
async def chat(project_id: str, request: "ChatRequest"):
    """Send a single user message to the Coder and return the reply.

    Unlike ``/start`` this does NOT kick off the Coder <-> Reviewer
    loop. It's a conversational endpoint: the user types something,
    the Coder responds once, the response comes back over the wire
    + a WebSocket event so the chat thread can render it.

    Use cases (Round 37):
      - "What does this function do?"
      - "Explain the difference between X and Y."
      - "Suggest a name for this module."
      - Quick questions that don't need a multi-round loop.

    For anything that involves writing files / running tools / making
    commits, the user clicks "Run as task" and the chat composer
    posts to ``/start`` instead.
    """
    try:
        project = _orch().get_project(project_id)
        if not project:
            raise HTTPException(status_code=404,
                                detail=f"Project not found: {project_id}")
        if not project.coder:
            # R38.6.4: surface the underlying attach errors so the
            # user can see WHY the Coder wasn't wired (MCP failure,
            # worktree error, provider init issue, etc). Without
            # this the user just sees "no coder" and doesn't know
            # whether to fix settings, restart the backend, or
            # delete the project.
            #
            # Don't append a generic "open Settings" hint here —
            # the frontend (Chat.tsx) already adds that based on
            # status/msg regex. Adding it server-side produces
            # double "open Settings" in the toast.
            errs = list(getattr(project.runtime, "attach_errors", []) or [])
            detail = "No Coder agent wired for this project"
            if errs:
                detail += f" — agent attach errors: {'; '.join(errs)}"
            raise HTTPException(status_code=503, detail=detail)

        text = (request.message or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="message is required")

        # R38.6.3: /chat calls the Coder's ``chat()`` method
        # (not ``run()``). ``chat()`` uses MAX_CHAT_TURNS=5 with
        # a single conversational prompt — no tool calls, no
        # Reviewer, no multi-round plan. The previous code used
        # ``run()`` which has MAX_TOOL_TURNS=25 and ran the full
        # tool loop, which is wrong for a single-turn chat message.
        # The user reported "Turn 1/25 + Reviewer triggered" for
        # a simple "你好" — this fix routes the chat through
        # ``chat()`` so it's a single LLM call.
        try:
            reply = await project.coder.chat(text)
        except Exception as exc:
            raise HTTPException(status_code=500,
                                detail=f"Coder chat failed: {exc}")
    except HTTPException:
        # Already a clean 4xx/5xx — let it through.
        raise
    except Exception as exc:
        # R38.6 §22: an unhandled exception in the pre-Coder logic
        # (e.g. _orch().get_project raises, project.coder is None
        # but accessed as attribute, message_bus is None, AgentTask
        # constructor fails) used to return FastAPI's default
        # plain-text "Internal Server Error" with NO detail. The
        # frontend then showed the bare "[500] Request failed with
        # status code 500" toast. Now we convert to a proper
        # 500 with detail so the user sees the real cause.
        logger.exception("chat pre-coder logic failed")
        raise HTTPException(status_code=500,
                            detail=f"chat pre-coder: {type(exc).__name__}: {exc}")
    # The Coder already published the reply as `agent.chat` on the bus
    # (from ``coder.chat()``), which the chat thread renders as the
    # reply bubble. Publishing a second `agent.chat_reply` event here
    # only doubles the WS traffic — nothing consumes it. The REST
    # response carries the reply for callers that don't watch the WS.
    return {"project_id": project_id, "reply": reply, "mode": "chat"}


@router.post("/{project_id}/stop")
async def stop_loop(project_id: str):
    """User-initiated stop of the loop."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    stopped = _orch().stop_loop(project_id)
    return {"status": "stopping" if stopped else "no_loop_running",
            "project_id": project_id}


@router.get("/{project_id}/plan")
async def get_plan(project_id: str):
    """Get the Coder'"'"'s draft plan waiting for user approval (if any)."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    plan = _orch().get_plan(project_id)
    if plan is None:
        return {"pending": False, "text": "", "decision": None, "round": 0}
    return plan


@router.post("/{project_id}/plan/approve")
async def approve_plan(project_id: str):
    """User approves the Coder'"'"'s plan 鈥?loop continues with tool execution."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok = _orch().approve_plan(project_id)
    return {"status": "approved" if ok else "no_plan_pending",
            "project_id": project_id}


@router.post("/{project_id}/plan/reject")
async def reject_plan(project_id: str):
    """User rejects the plan 鈥?loop stops."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok = _orch().reject_plan(project_id)
    return {"status": "rejected" if ok else "no_plan_pending",
            "project_id": project_id}


# ---------------------------------------------------------------------------
# Skills hot-reload (round 8)
# ---------------------------------------------------------------------------


@router.post("/{project_id}/skills/reload")
async def reload_skills(project_id: str):
    """Force a re-scan of the project's skills directory.

    The SkillsWatcher already polls mtimes once a second, but the
    Settings drawer has a "Reload now" button for users who want
    immediate feedback after editing a skill file.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    result = _orch().reload_skills(project_id)
    return {"project_id": project_id, **result}


@router.get("/{project_id}/skills")
async def list_skills(project_id: str):
    """Return the names of all skills currently discovered for the project."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    result = _orch().reload_skills(project_id)
    return {"project_id": project_id, **result}


class AskAnswerRequest(BaseModel):
    answer: str


@router.get("/{project_id}/ask")
async def get_ask_state(project_id: str):
    """Get the Reviewer's pending question waiting for user answer (if any).

    Mirrors the `/plan` endpoint but for the Coder 鈫?user ask flow.
    The Loop emits an `ask_state` message on the WebSocket when this
    changes; the chat UI polls it as a fallback.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404,
                            detail=f"Project not found: {project_id}")
    # R38.6.3: the Orchestrator doesn't currently track
    # pending user-input "ask" requests in a structured way
    # (this is a future feature). For now, return a stub
    # pending=False so the frontend's "is there a question
    # waiting?" check doesn't 500.
    return {"pending": False, "question": "", "context": "", "round": 0}


@router.post("/{project_id}/ask/answer")
async def answer_ask(project_id: str, request: AskAnswerRequest):
    """Submit the user's answer to a pending ask; the loop resumes."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404,
                            detail=f"Project not found: {project_id}")
    if not (request.answer or "").strip():
        raise HTTPException(status_code=400, detail="answer is required")
    ok = _orch().answer_ask(project_id, request.answer)
    return {"status": "answered" if ok else "no_ask_pending",
            "project_id": project_id}


@router.get("/{project_id}/loop")
async def get_loop_state(project_id: str):
    """Inspect current loop state: round, last score, last issues, etc."""
    project = _orch().get_project(project_id)
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


@router.get("/{project_id}/sessions")
async def list_loop_sessions(project_id: str):
    """List all loop sessions for a project, newest first.

    Each session is a single `kairos exec`-style run: from the moment
    the user clicks "start" until the loop converges, stops, or is
    killed. The new chat-style UI renders one row per session in the
    left sidebar so the user can browse past runs.

    The currently-running in-memory session is appended at the top if
    it isn't already in the persisted list (it won't have any rounds
    saved yet but we still want it visible in the sidebar).
    """
    from api.deps import orchestrator as _orch
    project = _orch.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    persisted = _orch._db.list_loop_sessions(project_id)  # type: ignore[attr-defined]
    # Promote the live in-memory session (if any) to the top of the
    # list 鈥?it might not have any rounds yet, but the user expects
    # to see "the current run" highlighted in the sidebar.
    in_mem = None
    sess = project.loop_session
    if sess and sess.session_id:
        in_mem = {
            "session_id": sess.session_id,
            "round_count": int(getattr(sess, "round", 0) or 0),
            "last_round": int(getattr(sess, "round", 0) or 0),
            "last_score": int(getattr(sess, "last_score", 0) or 0),
            "last_approve": bool(getattr(sess, "last_approve", False)),
            "started_at": float(getattr(sess, "started_at", 0) or 0),
            "last_activity": float(getattr(sess, "started_at", 0) or 0),
            "running": bool(project.loop_task and not project.loop_task.done()),
        }
    if in_mem and in_mem["session_id"] not in {p["session_id"] for p in persisted}:
        persisted.insert(0, in_mem)
    else:
        # Mark whichever one is currently running.
        if in_mem:
            for p in persisted:
                if p["session_id"] == in_mem["session_id"]:
                    p["running"] = bool(project.loop_task
                                         and not project.loop_task.done())
    return {"project_id": project_id, "sessions": persisted}


@router.get("/{project_id}/sessions/{session_id}/rounds")
async def get_session_rounds(project_id: str, session_id: str):
    """All rounds of one session, oldest first.

    Used by the chat thread to rebuild a session's full conversation
    history (Coder summary, Reviewer verdict, per-round issues)."""
    from api.deps import orchestrator as _orch
    project = _orch.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    rows = _orch._db.load_session_rounds(project_id, session_id)  # type: ignore[attr-defined]
    # Pull the in-memory session if it matches (it has the freshest
    # state 鈥?last round, plan/ask markers, etc. 鈥?even if it isn't
    # yet persisted to disk).
    sess = project.loop_session
    if sess and sess.session_id == session_id and getattr(sess, "history", None):
        # Append any history rows whose round isn't already on disk.
        seen_rounds = {int(r.get("round", -1)) for r in rows}
        for h in sess.history:
            r = int(h.get("round", -1))
            if r in seen_rounds:
                continue
            review = h.get("review") or {}
            rows.append({
                "project_id": project_id,
                "session_id": session_id,
                "round": r,
                "coder_summary": h.get("coder_summary", ""),
                "review_summary": (review.get("summary") or ""),
                "review_json": json.dumps(review) if review else None,
                "score": int(review.get("score", 0) or 0),
                "approve": 1 if review.get("approve") else 0,
                "created_at": float(h.get("created_at", 0) or 0),
            })
        rows.sort(key=lambda r: (r.get("created_at", 0), r.get("round", 0)))
    return {"project_id": project_id, "session_id": session_id, "rounds": rows}


@router.get("/{project_id}/settings")
async def get_project_settings(project_id: str):
    """Per-project settings (currently just the Coder sub-mode + env overrides)."""
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    md = dict(project.metadata or {})
    return {
        "project_id": project_id,
        "coder_mode": md.get("coder_mode", "default"),
        "metadata": md,
    }


@router.post("/{project_id}/settings")
async def update_project_settings(project_id: str, patch: dict):
    """Update per-project settings (e.g. Coder sub-mode).

    Currently the only meaningful field is ``coder_mode``. The
    endpoint is structured so additional per-project knobs can be
    added without changing the URL.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    md = dict(project.metadata or {})
    if "coder_mode" in (patch or {}):
        from kairos.coder_modes import CoderMode
        mode = CoderMode.parse(patch["coder_mode"])
        md["coder_mode"] = mode.value
        project.metadata = md
        if hasattr(project.runtime, "coder_mode"):
            project.runtime.coder_mode = mode.value
    return {
        "project_id": project_id,
        "coder_mode": md.get("coder_mode", "default"),
        "metadata": md,
    }


@router.get("/{project_id}/cost")
async def get_project_cost(project_id: str):
    """Token usage + USD cost summary for a project.

    Returns the project's aggregate plus the global cost
    summary so the UI can show a per-project card and a
    site-wide total in one request.
    """
    from kairos.cost import get_tracker
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    tracker = get_tracker()
    by_proj = tracker.by_project()
    proj_summary = by_proj.get(project_id)
    if proj_summary is None:
        # No usage recorded yet — return an empty summary
        proj_summary = type("Empty", (), {
            "to_dict": lambda self: {
                "project_id": project_id,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "calls": 0,
                "by_agent": {},
                "by_model": {},
                "by_day": {},
            }
        })()
    return {
        "project": proj_summary.to_dict(),
        "global": tracker.summary(),
    }


@router.get("/{project_id}/health")
async def get_loop_health(project_id: str):
    """Real-time loop health score (0-100). 100 = green, <=20 = critical.

    Combines score trend, infra-failure streak, and no-progress counter
    into one number so the UI can show a color-coded badge without
    re-deriving the formula on the frontend.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    session = project.loop_session
    if not session:
        body = {"health": 100, "running": False, "round": 0,
                "factors": {"score_trend": [], "infra_streak": 0, "no_progress": 0}}
    else:
        health = _loop_health_score(session)
        factors = {
            "score_trend": list(getattr(session, "score_window", []) or []),
            "infra_streak": session.infra_failure_streak,
            "no_progress": session.no_progress_count,
        }
        body = {
            "health": health,
            "running": bool(project.loop_task and not project.loop_task.done()),
            "round": session.round,
            "factors": factors,
        }
    # Surface memory stats so the UI can render a "what the agent has
    # learned" panel alongside the health badge. Best-effort: failure
    # to read memory tables never breaks the health response.
    try:
        db = getattr(orchestrator, "_db", None)
        if db is not None:
            notes = db.list_project_notes(project_id, limit=50) or []
            skills = db.list_skills(project_id) or []
            body["memory"] = {
                "notes_count": len(notes),
                "skills_count": len(skills),
                "top_notes": [
                    {"id": n.get("id"), "title": n.get("title"),
                     "kind": n.get("kind"), "use_count": n.get("use_count")}
                    for n in notes[:5]
                ],
                "top_skills": [
                    {"id": s.get("id"), "name": s.get("name"),
                     "confidence": s.get("confidence"),
                     "use_count": s.get("use_count")}
                    for s in skills[:5]
                ],
            }
    except Exception:
        body["memory"] = {"notes_count": 0, "skills_count": 0,
                          "top_notes": [], "top_skills": []}
    return body


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
    project = _orch().get_project(project_id)
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
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {"project_id": project_id, "best_of_n": project.best_of_n}


@router.post("/{project_id}/coder_mode")
async def set_coder_mode(project_id: str, request: dict):
    """Set the Coder sub-mode for a project.

    Body: ``{"mode": "default" | "read_only" | "sandbox"}``.

    The change takes effect on the next agent rebuild. For a running
    project, we also push the new mode into the existing Coder agent's
    metadata so the prompt hint updates immediately (the tool list
    itself is rebuilt only at agent construction time, which is when
    the policy actually filters tools).
    """
    from kairos.coder_modes import CoderMode
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    raw = (request or {}).get("mode", "default")
    mode = CoderMode.parse(raw)
    project.metadata = dict(project.metadata or {})
    project.metadata["coder_mode"] = mode.value
    project.runtime.coder_mode = mode.value
    return {
        "project_id": project_id,
        "mode": mode.value,
        "effective_next_rebuild": True,
    }


@router.get("/{project_id}/coder_mode")
async def get_coder_mode(project_id: str):
    from kairos.coder_modes import CoderMode
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {
        "project_id": project_id,
        "mode": getattr(project.runtime, "coder_mode", "default"),
        "policy": getattr(project.runtime, "coder_policy", None),
        "hint": __import__("kairos.coder_modes", fromlist=["hint_for_mode"]).hint_for_mode(
            CoderMode.parse(getattr(project.runtime, "coder_mode", "default"))
        ),
    }
    return {"best_of_n": getattr(project, "best_of_n", 1) or 1}


# Global message stream 鈥?see #27 / #28 for why this is at /api/messages
# (and not under /projects) 鈥?FastAPI'"'"'s dynamic-segment matching shadows
# literal paths under the same prefix.
@router.get("/messages")
async def get_global_messages(limit: int = 100):
    return {"messages": _orch().get_message_history(limit=limit)}


@router.get("/{project_id}/messages")
async def get_project_messages(project_id: str, limit: int = 50):
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    messages = _orch().get_message_history(limit=limit, project_id=project_id)
    return {"messages": messages}


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """Archive (soft-delete) a project.

    R38.6: this no longer hard-deletes the row. We just set
    ``archived_at`` so the row is hidden from ``load_projects()``.
    The project record, sessions, files, and notes are preserved
    so the user can restore via ``POST /api/projects/{id}/restore``.
    The in-memory project + agents are torn down so the running
    loop stops, but the data on disk stays intact.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    _orch().delete_project(project_id)
    return {"status": "archived",
            "message": f"Project {project_id} archived (data preserved)"}


@router.post("/{project_id}/restore")
async def restore_project(project_id: str):
    """Restore an archived project.

    R38.6: reverses a soft-delete. Clears ``archived_at`` so the
    row shows up in ``load_projects()`` again. The project's
    sessions, files, and notes were preserved on archive, so
    nothing is lost — the project comes back fully intact.
    """
    if not _orch().get_project(project_id):
        # The project might not be in memory (e.g. we just
        # restarted). The DB-level restore still works.
        pass
    ok = _orch().restore_project(project_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Project not found (or not archived): {project_id}")
    return {"status": "restored",
            "message": f"Project {project_id} restored"}



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
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    files = _orch().list_reference_files(project_id)
    return {"files": files}

@router.get("/{project_id}/files/{file_id}")
async def get_reference_file(project_id: str, file_id: str):
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    record = _orch().get_reference_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    if record.get("project_id") != project_id:
        raise HTTPException(status_code=403, detail="File does not belong to this project")
    return record

# 5 MB cap 鈥?reference files are kept inline in SQLite, so we have
# to guard against accidental uploads of large files that would bloat
# the DB and slow every subsequent read.
MAX_REFERENCE_FILE_BYTES = 5 * 1024 * 1024

@router.post("/{project_id}/files")
async def upload_reference_file(project_id: str, file: UploadFile = File(...)):
    """Upload a reference file to a project.

    The multipart payload is a single `file` part; the filename and
    content type come from the part itself, not from separate Form
    fields (this matches the FastAPI `UploadFile` convention and keeps
    the UI simple). The endpoint enforces a 5 MB cap to keep SQLite
    happy and returns 413 when the upload is too large.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    name = file.filename or "upload.bin"
    mime = file.content_type or "application/octet-stream"
    content = await file.read()
    if len(content) > MAX_REFERENCE_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_REFERENCE_FILE_BYTES} bytes)"
        )
    file_id = _orch().add_reference_file(project_id, name, mime, content)
    return {
        "status": "ok",
        "file": {
            "id": file_id,
            "name": name,
            "size": len(content),
            "mime": mime,
        },
    }

@router.delete("/{project_id}/files/{file_id}")
async def delete_reference_file(project_id: str, file_id: str):
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    record = _orch().get_reference_file(file_id)
    if record is None or record.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    ok = _orch().delete_reference_file(file_id)
    return {"status": "deleted" if ok else "not_found", "file_id": file_id}

# ============================================================================
# Per-file revert (uses git SHA captured at auto-checkpoint)
# ============================================================================

@router.post("/{project_id}/checkpoint/revert_file")
async def revert_file(project_id: str, request: "RevertFileRequest"):
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    ok, err = _orch().revert_file(project_id, request.sha, request.path)
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


class ChatRequest(BaseModel):
    """Round 37: payload for the single-turn /chat endpoint."""
    message: str
    # R38.6 §34: when true, the agent's task starts with
    # the Plan Mode flow — the LLM lays out a TODO and the
    # user must approve it before execution begins. Mirrors
    # the the the plan-mode pattern's "Plan Mode" toggle.
    require_plan: bool = False
    # Pre-generated plan_id (from /api/borrowed/.../plan/generate)
    # — when set, the agent reads the plan's step list and
    # asks the user to approve each step before executing.
    plan_id: str = ""

class RevertFileRequest(BaseModel):
    sha: str
    path: str

