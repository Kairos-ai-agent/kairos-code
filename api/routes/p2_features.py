"""R38.6 §34 - P2 borrowed features."""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/borrowed", tags=["borrowed-p2"])


# Verification trigger (the cloud task-style)
class VerifyBody(BaseModel):
    focus: str = ""


# ---------------------------------------------------------------------------
# R38.6.4 (long-running-harness-inspired): long-running features
# ---------------------------------------------------------------------------
@router.get("/{project_id}/subagent/handle/{handle}")
async def subagent_status(project_id: str, handle: str):
    """Snapshot of a background subagent spawned via
    ``background: true`` on spawn_subagent."""
    from kairos.long_running import get_registry
    rec = get_registry().status_of(handle)
    if rec is None or rec.get("project_id") != project_id:
        raise HTTPException(404, f"subagent handle not found: {handle}")
    return rec


@router.post("/{project_id}/subagent/handle/{handle}/cancel")
async def subagent_cancel(project_id: str, handle: str):
    """Best-effort cancel."""
    from kairos.long_running import get_registry
    rec = get_registry().status_of(handle)
    if rec is None or rec.get("project_id") != project_id:
        raise HTTPException(404, f"subagent handle not found: {handle}")
    ok = get_registry().cancel(handle)
    return {"ok": ok, "handle": handle}


class SetGoalBody(BaseModel):
    text: str


@router.put("/{project_id}/goal")
async def set_goal(project_id: str, body: SetGoalBody):
    """Set or clear the project's persistent goal (long-running-harness /goal).
    Every new task in the session picks this up as context."""
    from kairos.long_running import get_registry
    get_registry().set_goal(project_id, body.text)
    return {"ok": True, "goal": body.text}


@router.get("/{project_id}/goal")
async def get_goal(project_id: str):
    from kairos.long_running import get_registry
    return {"goal": get_registry().get_goal(project_id)}


class AutonomousBody(BaseModel):
    requirement: str = ""  # actual task text (may be in message)
    task: str = ""         # alternate field name
    max_turns: int = 20
    time_budget_s: int = 1800
    gate: str = ""  # optional shell command / label


@router.post("/{project_id}/autonomous")
async def start_autonomous(project_id: str, body: AutonomousBody):
    """Submit a long-running autonomous task (long-running-harness
    /autonomous). Returns a job_id the frontend can poll."""
    from kairos.long_running import get_registry
    job_id = get_registry().register_autonomous(
        project_id=project_id,
        max_turns=body.max_turns,
        time_budget_s=body.time_budget_s,
        gate=body.gate,
    )
    from kairos.core.message_bus import Message
    try:
        await _orch().message_bus.publish(Message(
            sender="user", topic="autonomous.submitted",
            content=body.requirement[:500], msg_type="text",
            metadata={"project_id": project_id, "job_id": job_id,
                      "max_turns": body.max_turns,
                      "time_budget_s": body.time_budget_s,
                      "gate": body.gate},
        ))
    except Exception:
        pass
    return {"job_id": job_id, "status": "queued"}


@router.get("/{project_id}/autonomous/jobs")
async def list_autonomous(project_id: str):
    from kairos.long_running import get_registry
    return {"jobs": get_registry().list_autonomous(project_id)}


@router.get("/{project_id}/autonomous/jobs/{job_id}")
async def get_autonomous_job(project_id: str, job_id: str):
    from kairos.long_running import get_registry
    rec = get_registry().get_autonomous(job_id)
    if rec is None or rec.get("project_id") != project_id:
        raise HTTPException(404, f"autonomous job not found: {job_id}")
    return rec


@router.post("/{project_id}/verify")
async def run_verification(project_id: str, body: VerifyBody):
    from api.routes.projects import _orch
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found: " + project_id)
    if not project.reviewer:
        raise HTTPException(503, "No Reviewer wired")
    from kairos.agents.base import AgentTask
    task = AgentTask(
        id="verify-" + uuid.uuid4().hex[:8],
        title="User-triggered verification",
        description=body.focus or "Re-verify the project state.",
        priority="normal",
    )
    try:
        result = await project.reviewer.run(task)
        return {
            "ok": True,
            "verdict": getattr(result, "decision", "pass"),
            "summary": getattr(result, "summary", ""),
            "findings": getattr(result, "findings", []),
        }
    except Exception as exc:
        logger.exception("verification failed")
        return {"ok": False, "error": str(exc)}


# Approval mode per project
@router.get("/{project_id}/approval")
async def get_approval_mode(project_id: str):
    from api.routes.projects import _orch
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found: " + project_id)
    from kairos.approval import ApprovalMode
    mode_name = getattr(project.runtime, "approval_mode", "suggest") \
        if hasattr(project, "runtime") and project.runtime else "suggest"
    mode = ApprovalMode.parse(mode_name)
    return {"mode": mode.value, "label": mode.describe()}


class SetApprovalBody(BaseModel):
    mode: str


@router.put("/{project_id}/approval")
async def set_approval_mode(project_id: str, body: SetApprovalBody):
    from api.routes.projects import _orch
    from kairos.approval import ApprovalMode
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found: " + project_id)
    mode = ApprovalMode.parse(body.mode)
    if not hasattr(project, "runtime") or project.runtime is None:
        from kairos.core.project_runtime import ProjectRuntime
        project.runtime = ProjectRuntime()
    project.runtime.approval_mode = mode.value
    return {"ok": True, "mode": mode.value, "label": mode.describe()}


# Thread/Turn/Item event stream (the cloud task-style)
_event_log: Dict[str, List[Dict[str, Any]]] = {}


@router.post("/{project_id}/events")
async def publish_event(project_id: str, body: Dict[str, Any]):
    event = {"id": uuid.uuid4().hex[:12], "ts": time.time(), **body}
    _event_log.setdefault(project_id, []).append(event)
    if len(_event_log[project_id]) > 500:
        _event_log[project_id] = _event_log[project_id][-500:]
    return {"ok": True, "id": event["id"]}


@router.get("/{project_id}/events")
async def list_events(project_id: str, since: float = 0.0, topic: str = ""):
    events = _event_log.get(project_id, [])
    out = [e for e in events
           if e.get("ts", 0) > since
           and (not topic or e.get("topic") == topic)]
    return {"events": out}


@router.get("/{project_id}/events/stream")
async def stream_events(project_id: str, request: Request):
    from fastapi.responses import StreamingResponse

    async def gen():
        last = 0.0
        try:
            while True:
                if await request.is_disconnected():
                    break
                events = _event_log.get(project_id, [])
                fresh = [e for e in events if e.get("ts", 0) > last]
                for e in fresh:
                    last = e.get("ts", last)
                    yield "data: " + json.dumps(e, ensure_ascii=False) + "\n\n"
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(gen(), media_type="text/event-stream")


# Cloud async tasks (the cloud task-style background jobs)
_jobs: Dict[str, Dict[str, Any]] = {}


class AsyncTaskBody(BaseModel):
    task: str
    project_id: str = ""


@router.post("/async/submit")
async def submit_async_task(body: AsyncTaskBody):
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "id": job_id, "task": body.task, "project_id": body.project_id,
        "status": "queued", "created_at": time.time(),
        "result": None, "error": None,
    }
    asyncio.create_task(_run_async_job(job_id, body.task, body.project_id))
    return {"job_id": job_id, "status": "queued"}


async def _run_async_job(job_id: str, task: str, project_id: str):
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["started_at"] = time.time()
    try:
        await asyncio.sleep(1)
        try:
            from api.routes.projects import _orch
            project = _orch().get_project(project_id) if project_id else None
            if project and project.coder:
                from kairos.agents.base import AgentTask
                t = AgentTask(id=uuid.uuid4().hex[:12],
                              title="Async: " + task[:60],
                              description=task, priority="low")
                result = await project.coder.run(t)
                _jobs[job_id]["result"] = {
                    "summary": getattr(result, "summary", ""),
                    "text": getattr(result, "text", str(result)),
                }
            else:
                _jobs[job_id]["result"] = {
                    "summary": "(no coder for project)",
                    "text": "Task queued: " + task[:200],
                }
        except Exception as inner:
            _jobs[job_id]["result"] = {"text": "Error: " + str(inner)}
        _jobs[job_id]["status"] = "done"
    except Exception as exc:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(exc)
    finally:
        _jobs[job_id]["finished_at"] = time.time()


@router.get("/async/jobs/{job_id}")
async def get_async_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found: " + job_id)
    return job


@router.get("/async/jobs")
async def list_async_jobs(project_id: str = ""):
    out = [j for j in _jobs.values()
           if not project_id or j.get("project_id") == project_id]
    out.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return {"jobs": out[:50]}


# LSP integration (the multi-model CLI-style)
class LspCheckBody(BaseModel):
    file_path: str


@router.post("/{project_id}/lsp/check")
async def lsp_check(project_id: str, body: LspCheckBody):
    from api.routes.projects import _orch
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found: " + project_id)
    work_dir = project.work_dir or project.workspace or "."
    target = Path(work_dir) / body.file_path
    if not target.exists():
        return {"ok": False, "error": "file not found: " + body.file_path}
    if target.suffix == ".py":
        import subprocess
        try:
            r = subprocess.run(
                ["python", "-m", "py_compile", str(target)],
                capture_output=True, text=True, timeout=10,
            )
            diagnostics = []
            if r.returncode != 0:
                for line in (r.stderr or "").splitlines():
                    if "line" in line and "File" not in line:
                        try:
                            line_no = int(line.split("line")[-1].split()[0])
                            msg = line.split("^")[0].strip()
                            diagnostics.append({
                                "line": line_no, "severity": "error",
                                "message": msg,
                            })
                        except (ValueError, IndexError):
                            pass
            return {
                "ok": r.returncode == 0,
                "diagnostics": diagnostics,
                "file": body.file_path,
                "engine": "py_compile",
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "py_compile timed out"}
    return {
        "ok": True,
        "diagnostics": [],
        "file": body.file_path,
        "engine": "none",
        "note": "no LSP server configured for " + str(target.suffix),
    }


# RL trajectory export (self-improving style)
class TrajectoryBody(BaseModel):
    output_path: str = ""


@router.post("/{project_id}/trajectory/export")
async def export_trajectory(project_id: str, body: TrajectoryBody):
    from api.routes.projects import _orch
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found: " + project_id)
    work_dir = project.work_dir or project.workspace or "."
    out_dir = Path(work_dir) / ".kairos" / "trajectories"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(body.output_path) if body.output_path \
               else out_dir / (str(int(time.time())) + ".jsonl")
    rows = []
    if project.coder and hasattr(project.coder, "_memory"):
        for m in project.coder._memory[-200:]:
            try:
                rows.append({
                    "role": getattr(m, "role", "user"),
                    "content": getattr(m, "content", "")[:1000],
                    "ts": getattr(m, "ts", time.time()),
                    "tool_calls": [
                        {
                            "name": tc.name,
                            "args": str(tc.arguments)[:200],
                            "result": str(getattr(tc, "result", ""))[:200],
                        }
                        for tc in (getattr(m, "tool_calls", None) or [])
                    ],
                })
            except Exception:
                continue
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"ok": True, "path": str(out_path), "rows": len(rows)}


# A2A remote agent (Gemini-style)
class A2ARegisterBody(BaseModel):
    name: str
    endpoint: str
    auth_token: str = ""


_a2a_agents: Dict[str, Dict[str, Any]] = {}


@router.post("/a2a/register")
async def register_a2a_agent(body: A2ARegisterBody):
    agent_id = uuid.uuid4().hex[:8]
    _a2a_agents[agent_id] = {
        "id": agent_id, "name": body.name,
        "endpoint": body.endpoint,
        "auth_token": body.auth_token,
        "registered_at": time.time(),
        "status": "unknown",
    }
    try:
        import httpx
        with httpx.Client(timeout=5) as client:
            r = client.get(body.endpoint)
        _a2a_agents[agent_id]["status"] = (
            "reachable" if r.status_code < 500 else "unreachable")
    except Exception as exc:
        _a2a_agents[agent_id]["status"] = "unreachable: " + str(exc)
    return _a2a_agents[agent_id]


@router.get("/a2a/agents")
async def list_a2a_agents():
    return {"agents": list(_a2a_agents.values())}


@router.delete("/a2a/agents/{agent_id}")
async def unregister_a2a_agent(agent_id: str):
    return {"ok": _a2a_agents.pop(agent_id, None) is not None}


class A2ACallBody(BaseModel):
    agent_id: str
    message: str


@router.post("/a2a/call")
async def call_a2a_agent(body: A2ACallBody):
    import httpx
    agent = _a2a_agents.get(body.agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found: " + body.agent_id)
    payload = {
        "jsonrpc": "2.0", "id": uuid.uuid4().hex[:8],
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": body.message}],
            }
        },
    }
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(agent["endpoint"], json=payload)
        if r.status_code >= 400:
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
        return {"ok": True, "reply": r.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# R38.6.4: Continual Harness (long-running-harness-inspired)
# ---------------------------------------------------------------------------
class HarnessSuppPromptBody(BaseModel):
    text: str


class HarnessMemoryNoteBody(BaseModel):
    key: str
    value: str
    tags: List[str] = []


class HarnessSkillHintBody(BaseModel):
    pattern: str
    hint: str
    source: str = "agent"


class HarnessToolingPrefBody(BaseModel):
    key: str
    value: str


def _project_work_dir(project_id):
    from api.routes.projects import _orch
    project = _orch().get_project(project_id) if _orch() else None
    if project is None:
        return Path.cwd()
    return Path(getattr(project, "work_dir", None) or getattr(project,
                                                       "workspace", None)
                or Path.cwd())


@router.get("/{project_id}/harness")
async def harness_get(project_id: str):
    from kairos.continual_harness import HarnessStore
    return HarnessStore(_project_work_dir(project_id)).load()


@router.put("/{project_id}/harness/supplemental-prompt")
async def harness_set_supp(project_id: str, body: HarnessSuppPromptBody):
    from kairos.continual_harness import HarnessStore
    s = HarnessStore(_project_work_dir(project_id))
    s.set_supplemental_prompt(body.text)
    return {"ok": True, "data": s.load()}


@router.post("/{project_id}/harness/memory-note")
async def harness_add_note(project_id: str, body: HarnessMemoryNoteBody):
    from kairos.continual_harness import HarnessStore
    s = HarnessStore(_project_work_dir(project_id))
    s.add_memory_note(body.key, body.value, body.tags)
    return {"ok": True, "data": s.load()}


@router.post("/{project_id}/harness/skill-hint")
async def harness_add_hint(project_id: str, body: HarnessSkillHintBody):
    from kairos.continual_harness import HarnessStore
    s = HarnessStore(_project_work_dir(project_id))
    s.add_skill_hint(body.pattern, body.hint, body.source)
    return {"ok": True, "data": s.load()}


@router.put("/{project_id}/harness/tooling-pref")
async def harness_set_pref(project_id: str, body: HarnessToolingPrefBody):
    from kairos.continual_harness import HarnessStore
    s = HarnessStore(_project_work_dir(project_id))
    s.set_tooling_pref(body.key, body.value)
    return {"ok": True, "data": s.load()}


@router.delete("/{project_id}/harness/{key}")
async def harness_remove(project_id: str, key: str):
    from kairos.continual_harness import HarnessStore
    s = HarnessStore(_project_work_dir(project_id))
    s.remove(key)
    return {"ok": True, "data": s.load()}


@router.get("/{project_id}/harness/history")
async def harness_history(project_id: str, limit: int = 20):
    from kairos.continual_harness import HarnessStore
    s = HarnessStore(_project_work_dir(project_id))
    return {"entries": s.history_tail(limit)}


@router.post("/{project_id}/harness/rollback/{history_ts}")
async def harness_rollback(project_id: str, history_ts: str):
    from kairos.continual_harness import HarnessStore
    s = HarnessStore(_project_work_dir(project_id))
    ok = s.rollback(history_ts)
    return {"ok": ok}


# ---------------------------------------------------------------------------
# R38.6.4: Subprocess sandbox for tool execution
# ---------------------------------------------------------------------------
class RunCommandBody(BaseModel):
    command: str
    cwd: str = ""
    timeout_s: int = 30


@router.post("/{project_id}/sandbox/run")
async def sandbox_run(project_id: str, body: RunCommandBody):
    import subprocess as _subprocess
    cwd = body.cwd or str(_project_work_dir(project_id))
    try:
        proc = _subprocess.run(body.command, shell=True, cwd=cwd,
                                capture_output=True, text=True,
                                timeout=body.timeout_s)
        return {"stdout": proc.stdout[:8000],
                "stderr": proc.stderr[:4000],
                "returncode": proc.returncode, "timed_out": False}
    except _subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "command timed out",
                "returncode": -1, "timed_out": True}
    except Exception as exc:
        return {"stdout": "", "stderr": f"{type(exc).__name__}: {exc}",
                "returncode": -1, "timed_out": False}


# ---------------------------------------------------------------------------
# R38.6.4: JSONL persistence state path for daemon restarts
@router.get("/persist/state")
async def persist_state_get():
    """Read the on-disk long_running.jsonl state."""
    from kairos.long_running import get_registry
    reg = get_registry()
    pp = getattr(reg, "_persist_path", None)
    if not pp:
        return {"persisted": False,
                "reason": "registry has no _persist_path"}
    if not pp.exists():
        return {"persisted": True, "path": str(pp), "records": 0}
    lines = pp.read_text(encoding="utf-8").splitlines()
    last_5 = []
    for ln in lines[-5:]:
        ln = ln.strip()
        if ln:
            try:
                last_5.append(json.loads(ln))
            except Exception:
                pass
    return {"persisted": True, "path": str(pp),
            "records": len(lines), "last_5_records": last_5}


# R38.6.4: Provider model-id validation
# ---------------------------------------------------------------------------
class ValidateModelBody(BaseModel):
    endpoint_url: str
    api_key: str
    model: str


@router.post("/validate-model")
async def validate_model(body: ValidateModelBody):
    """Validate a (provider endpoint, model id) by hitting ``GET /v1/models``.

    Uses httpx with ``trust_env=False`` so it bypasses the system
    HTTP_PROXY (which on Windows is often an unreachable local proxy).
    Supports both OpenAI-style (``/v1/models``) and Anthropic-style
    endpoints (Anthropic does not expose ``/v1/models`` — falls back
    to a heuristic match against a known list).
    """
    import httpx as _httpx
    if not body.endpoint_url or not body.api_key or not body.model:
        return {"valid": False, "reason": "missing url / key / model"}
    base = body.endpoint_url.rstrip("/")
    if "/chat/completions" in base:
        base = base.rsplit("/chat/completions", 1)[0]
    elif "/messages" in base:
        base = base.rsplit("/messages", 1)[0]
    models_url = base + "/models"
    headers = {"Authorization": f"Bearer {body.api_key}"}
    is_anthropic = "anthropic" in body.endpoint_url.lower()
    if is_anthropic:
        # Anthropic's /v1/models is the public model catalog (added 2025);
        # older proxies may not have it. If it fails, fall through.
        headers["anthropic-version"] = "2023-06-01"
        headers["x-api-key"] = body.api_key
    try:
        async with _httpx.AsyncClient(timeout=10, trust_env=False) as c:
            r = await c.get(models_url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            ids = [m.get("id") for m in data.get("data", data.get("models", []))]
            return {"valid": body.model in ids,
                    "matched": [i for i in ids if i][:50],
                    "count": len(ids)}
        # Some providers (notably Anthropic legacy) return 404 for /v1/models.
        # Fall through to heuristic check.
        return _heuristic_validate(body.model, is_anthropic,
                                   f"HTTP {r.status_code} from {models_url}")
    except Exception as exc:
        return _heuristic_validate(body.model, is_anthropic,
                                   f"{type(exc).__name__}: {exc}")


# Known-good model id allowlists for providers that don't expose /v1/models.
_KNOWN_ANTHROPIC_MODELS = {
    "claude-3-5-sonnet-latest", "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620", "claude-3-5-haiku-latest",
    "claude-3-5-haiku-20241022", "claude-3-opus-latest",
    "claude-3-opus-20240229", "claude-3-haiku-20240307",
    "claude-opus-4-1", "claude-opus-4-1-20250805",
    "claude-sonnet-4-5", "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5", "claude-haiku-4-5-20251001",
}
# MiniMax advertised (via /v1/models) + legacy + MiniMax M-series (the
# current generation as of 2026-08).
_KNOWN_MINIMAX_MODELS = {
    # current M-series (2026-08)
    "MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1", "MiniMax-M2.1-highspeed", "MiniMax-M2",
    # legacy text + VL
    "MiniMax-Text-01", "MiniMax-Text-02", "MiniMax-VL-01",
    # abab series (older)
    "abab6.5s-chat", "abab6.5-chat", "abab5.5-chat", "abab5.5s-chat",
}


def _heuristic_validate(model: str, is_anthropic: bool, reason: str) -> dict:
    """Fallback when /v1/models isn't available — check against a
    small known-model list for the provider. ``valid`` is True iff
    the model id is in the list."""
    pool = _KNOWN_ANTHROPIC_MODELS if is_anthropic else _KNOWN_MINIMAX_MODELS
    if model in pool:
        return {"valid": True, "matched": [model], "count": 1,
                "source": "heuristic", "note": reason}
    return {"valid": False, "reason": f"not in known list ({reason})",
            "known": sorted(pool)[:20], "source": "heuristic"}


# ---------------------------------------------------------------------------
# R38.6.4: Slash commands inside chat (e.g. /goal, /autonomous, /verify)
# ---------------------------------------------------------------------------
import re as _re
_SLASH_RE = _re.compile(r"^/(\w+)(?:\s+(.*))?$")


@router.post("/{project_id}/slash")
async def slash_command(project_id: str, body: dict):
    """Execute a single slash command in the chat thread.

    Supported: /goal <text>, /autonomous <req>, /compact, /verify,
    /forget <key>, /remember <key>=<value>.

    Returns a dict with ``reply`` (the user-facing message),
    optional ``state_change`` (UI hint), and ``continue_chat``
    (whether the original message should still be sent to the
    agent after the command).
    """
    text = (body or {}).get("text", "").strip()
    m = _SLASH_RE.match(text)
    if not m:
        return {"reply": f"Unrecognized command: {text}",
                "continue_chat": True}
    cmd, arg = m.group(1), (m.group(2) or "").strip()
    if cmd == "goal":
        from kairos.long_running import get_registry
        if not arg:
            return {"reply": f"Current goal: {get_registry().get_goal(project_id) or '(none)'}",
                    "continue_chat": True}
        get_registry().set_goal(project_id, arg)
        return {"reply": f"Goal set: {arg}", "continue_chat": False}
    if cmd == "autonomous":
        if not arg:
            return {"reply": "Usage: /autonomous <requirement>",
                    "continue_chat": True}
        from kairos.long_running import get_registry
        jid = get_registry().register_autonomous(
            project_id=project_id, max_turns=20,
            time_budget_s=1800, gate="")
        return {"reply": f"Autonomous job submitted: {jid}",
                "state_change": "show_autonomous",
                "continue_chat": False}
    if cmd == "compact":
        from kairos.compaction import maybe_compact
        return {"reply": "Compaction is automatic; the agent compacts every 10 turns.",
                "continue_chat": True}
    if cmd == "verify":
        return {"reply": "Verify is on the Tools → Verify tab; /verify alone does not run a check.",
                "continue_chat": True}
    if cmd == "forget":
        if not arg:
            return {"reply": "Usage: /forget <key>", "continue_chat": True}
        from kairos.memory_kb import MemoryKB
        from kairos.config.settings import settings as ksettings
        kb = MemoryKB(storage_path=ksettings.data_dir / "memory_kb.json")
        return {"reply": "Forgot: " + arg if kb.forget(arg, "project") else "Not found",
                "continue_chat": True}
    if cmd == "remember":
        if "=" not in arg:
            return {"reply": "Usage: /remember <key>=<value>", "continue_chat": True}
        k, v = arg.split("=", 1)
        k, v = k.strip(), v.strip()
        from kairos.memory_kb import MemoryKB
        from kairos.config.settings import settings as ksettings
        kb = MemoryKB(storage_path=ksettings.data_dir / "memory_kb.json")
        kb.remember(k, v, "project")
        return {"reply": f"Remembered {k!r}", "continue_chat": True}
    return {"reply": f"Unknown command: /{cmd}", "continue_chat": True}


# ---------------------------------------------------------------------------
# R38.6.4: A2A-style cross-agent messaging (long-running-harness inspired)
# ---------------------------------------------------------------------------
class SendAgentMessageBody(BaseModel):
    from_agent: str
    to_agent: str
    content: str
    project_id: str = ""
    kind: str = "note"   # note | question | deliverable


@router.post("/a2a/send")
async def a2a_send(body: SendAgentMessageBody):
    """One agent sends a message to another. Persisted in the
    message bus history. The receiving agent sees it as a new
    ``agent.message`` event next time it polls or runs a turn.

    Why not a separate A2A daemon: same reason as long_running.py -
    a process-local pub-sub is enough for a single-process dev
    setup. The message bus history acts as the inbox.
    """
    from kairos.core.message_bus import Message
    from api.routes.projects import _orch
    bus = _orch().message_bus if _orch() else None
    if bus is None:
        raise HTTPException(503, "message bus not available")
    msg = Message(
        sender=body.from_agent, receiver=body.to_agent,
        topic="agent.message", content=body.content,
        msg_type=body.kind,
        metadata={"project_id": body.project_id,
                  "kind": body.kind},
    )
    await bus.publish(msg)
    return {"ok": True, "delivered": True}


@router.get("/a2a/inbox/{agent_id}")
async def a2a_inbox(agent_id: str, since_ts: float = 0.0,
                     limit: int = 50):
    """Return messages addressed to ``agent_id`` since the given
    timestamp. The agent calls this between turns to pick up
    peer messages. ``since_ts=0`` returns all queued messages."""
    from api.routes.projects import _orch
    bus = _orch().message_bus if _orch() else None
    if bus is None:
        raise HTTPException(503, "message bus not available")
    history = await bus.recent(limit=500)
    out = []
    for m in history:
        rec = m.to_dict() if hasattr(m, "to_dict") else m
        if (rec.get("receiver") == agent_id
                and rec.get("timestamp", 0) > since_ts):
            out.append(rec)
    return {"messages": out[-limit:], "count": len(out)}


@router.get("/a2a/agents")
async def a2a_list_agents():
    """List known agent ids that have published recently (sender or
    receiver in last 200 events)."""
    from api.routes.projects import _orch
    bus = _orch().message_bus if _orch() else None
    if bus is None:
        raise HTTPException(503, "message bus not available")
    history = await bus.recent(limit=200)
    agents = set()
    for m in history:
        # ``history`` items are Message objects (not dicts) — use attributes,
        # not .get(), which would raise AttributeError -> HTTP 500.
        sender = getattr(m, "sender", None)
        receiver = getattr(m, "receiver", None)
        if sender:
            agents.add(sender)
        if receiver:
            agents.add(receiver)
    return {"agents": sorted(agents)}


# ---------------------------------------------------------------------------
# R38.6.4: Daemon status / attach
# ---------------------------------------------------------------------------
@router.get("/daemon/status")
async def daemon_status():
    from kairos.daemon import get_supervisor
    s = get_supervisor()
    if s is None:
        # Try reading the daemon.json that the supervisor wrote
        import json
        from pathlib import Path
        for path in (Path(".kairos/daemon.json"), Path.cwd() / ".kairos" / "daemon.json"):
            if path.exists():
                try:
                    return {"supervisor_running": False, **json.loads(
                        path.read_text(encoding="utf-8"))}
                except Exception:
                    pass
        return {"supervisor_running": False}
    return {"supervisor_running": True, **s.status_dict()}


@router.get("/daemon/attach/{session_id}")
async def daemon_attach(session_id: str):
    from kairos.daemon import get_supervisor
    s = get_supervisor()
    if s is None:
        raise HTTPException(503, "daemon supervisor not attached")
    return s.attach_session(session_id)


# ---------------------------------------------------------------------------
# R38.6.4: SWE-bench Lite harness baseline
# ---------------------------------------------------------------------------
class RunHarnessBody(BaseModel):
    task: str = ""  # empty = run all
    model: str = ""  # optional override for the run
    max_turns: int = 12


@router.post("/{project_id}/harness/run")
async def harness_run(project_id: str, body: RunHarnessBody):
    """Run the 10-task SWE-bench Lite harness against the project
    and report pass-rate. Uses the project's Coder to do the
    actual edits, then computes a unified diff and scores each
    task via SequenceMatcher against the ground-truth diff.

    R38.6.4 baseline: this is the metric long-running-harness / DeepSeek
    Harness use to claim 95.5% on ARC-AGI-3. We can't compare
    against ARC-AGI-3 directly (no API access) but the 10-task
    mini suite gives a fast, comparable signal.
    """
    from kairos.bench.harness_eval import (
        TASKS, run_full_eval, HarnessTask,
    )
    from api.routes.projects import _orch
    orch = _orch() if _orch() else None
    if orch is None:
        raise HTTPException(503, "orchestrator not available")
    project = orch.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"project not found: {project_id}")
    if project.coder is None:
        # R38.6.4: surface the underlying attach errors. The
        # frontend adds a generic "open Settings" hint based on
        # the 503 status, so we don't append it here (would be
        # duplicated in the toast).
        errs = list(getattr(project.runtime, "attach_errors", []) or [])
        detail = "No Coder agent wired for this project"
        if errs:
            detail += f" — agent attach errors: {'; '.join(errs)}"
        raise HTTPException(503, detail)

    async def coder_harness(task, work_dir):
        """Hand the task to the project's Coder, then return
        the unified diff of files in work_dir vs the starting
        state. We point the Coder at the task as a one-shot
        chat, no Reviewer loop."""
        from kairos.agents.base import AgentTask
        ag_task = AgentTask(
            id="harness-" + task.name,
            title=f"Harness task: {task.name}",
            description=(
                f"You are working in {work_dir}. The repo is "
                f"already set up. Task: {task.prompt}\n"
                f"After you finish, reply with 'DONE' on its own line."),
            instruction=task.prompt,
            context={"project_id": project_id,
                      "work_dir": str(work_dir),
                      "harness_task": task.name},
        )
        try:
            await project.coder.chat(ag_task.description or task.prompt)
        except Exception:
            pass
        # Compute the diff between the initial state and current.
        return _diff_repo(work_dir, task)

    tasks = ([next((t for t in TASKS if t.name == body.task), None)]
             if body.task else None)
    if body.task and tasks[0] is None:
        raise HTTPException(404, f"unknown task: {body.task}")
    selected = tasks or TASKS
    report = await run_full_eval(selected, harness=coder_harness)
    return report.to_dict()


def _diff_repo(work_dir, task):
    """Compute unified diff of the current work_dir state
    against the task's expected ground_truth_diff.

    For our simple bench, the task has a single expected change
    in one file. We return the diff of the actual file vs the
    expected file content embedded in the diff (we approximate
    by just returning whatever diff ``git diff --no-index`` would).
    """
    import subprocess
    # Use a simple per-file diff
    out = []
    for path, expected_content in task.repo_files.items():
        full = work_dir / path
        if not full.exists():
            out.append(f"--- a/{path} (missing)\n+++ b/{path}\n")
            continue
        actual = full.read_text(encoding="utf-8", errors="replace")
        if actual == expected_content:
            continue
        # crude per-line diff via difflib
        import difflib
        diff = difflib.unified_diff(
            expected_content.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}", n=2,
        )
        out.append("".join(diff))
    return "\n".join(out) or "no changes made"


# ---------------------------------------------------------------------------
# R38.6.4: Real LLM harness (3 scoring methods + per-model leaderboard)
# ---------------------------------------------------------------------------
import os as _os
_HARNESS_LEADERBOARD_PATH = Path(
    _os.environ.get("KAIROS_HARNESS_LEADERBOARD",
                    str(Path.home() / ".kairos" / "harness_leaderboard.json"))
)


def _load_leaderboard() -> list[dict]:
    try:
        if _HARNESS_LEADERBOARD_PATH.exists():
            return json.loads(
                _HARNESS_LEADERBOARD_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_leaderboard(entries: list[dict]) -> None:
    try:
        _HARNESS_LEADERBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HARNESS_LEADERBOARD_PATH.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as e:
        logger.warning(f"harness leaderboard save failed: {e}")


def _record_leaderboard(provider: str, model: str, method: str,
                        report: dict, usage: dict | None = None,
                        cost_usd: float = 0.0) -> None:
    entries = _load_leaderboard()
    entry = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provider": provider,
        "model": model,
        "method": method,
        "pass_rate": report.get("pass_rate", 0.0),
        "mean_score": report.get("mean_score", 0.0),
        "total_s": report.get("total_s", 0.0),
        "task_count": len(report.get("tasks", [])),
    }
    if usage:
        entry["input_tokens"] = usage.get("input_tokens", 0)
        entry["output_tokens"] = usage.get("output_tokens", 0)
        entry["total_tokens"] = usage.get("total_tokens", 0)
        entry["calls"] = usage.get("calls", 0)
        entry["cost_usd"] = round(cost_usd, 4)
    entries.append(entry)
    # Keep last 200 entries
    entries = entries[-200:]
    _save_leaderboard(entries)


class LLMHarnessBody(BaseModel):
    task: str = ""  # empty = run all 10
    method: str = "semantic"  # difflib | pytest | semantic
    provider: str = "minimax"  # minimax | openai | anthropic
    model: str = ""  # optional override; default per provider
    api_key: str = ""  # optional override; else env/file
    base_url: str = ""  # optional override
    persist: bool = True  # record to leaderboard


@router.get("/harness/tasks")
async def harness_list_tasks():
    """List the 10 SWE-bench Lite tasks (R38.6.4 baseline)."""
    from kairos.bench.harness_eval import TASKS
    return {
        "count": len(TASKS),
        "tasks": [
            {"name": t.name, "prompt": t.prompt,
             "category": _category(t.name)}
            for t in TASKS
        ],
    }


def _category(name: str) -> str:
    return {
        "off_by_one": "1-line fix",
        "add_input_validation": "validation",
        "convert_print_to_logging": "refactor",
        "extract_magic_number": "naming",
        "add_docstring": "docstring",
        "rename_function": "rename",
        "add_type_hints": "typing",
        "fix_broken_import": "import",
        "write_unit_test": "new file",
        "implement_function": "implement",
    }.get(name, "other")


@router.post("/harness/run-llm")
async def harness_run_llm(body: LLMHarnessBody):
    """Run the 10-task SWE-bench Lite harness against a real LLM
    (R38.6.4). Picks a scoring method: difflib / pytest / semantic.

    This is the per-model leaderboard endpoint: every run is recorded
    to ``~/.kairos/harness_leaderboard.json`` so you can compare
    different providers / models over time.

    Returns the per-task scores plus aggregate pass_rate / mean_score.
    """
    from kairos.bench.harness_eval import (
        TASKS as _TASKS, HarnessReport, HarnessResult,
    )
    import asyncio as _asyncio
    import shutil as _shutil
    import tempfile as _tempfile

    method = body.method.lower().strip()
    if method not in {"difflib", "pytest", "semantic"}:
        raise HTTPException(
            400, f"unknown method: {method} (use difflib|pytest|semantic)")

    # Pick the right scorer
    if method == "difflib":
        from kairos.bench.harness_eval import (
            run_task as _run_task, TASKS as _T)
        run_task_fn = _run_task
        score_kwargs = {}
    elif method == "pytest":
        from kairos.bench.harness_pytest_eval import (
            run_one_task as _run_one_task, apply_diff_to_workdir,
            find_test_files, run_pytest,
        )
        run_task_fn = None
    else:  # semantic
        from kairos.bench.harness_semantic_eval import (
            run_one_task as _run_one_task,
        )
        run_task_fn = None

    # Build the LLM harness function for the chosen provider
    selected = _TASKS
    if body.task:
        selected = [t for t in _TASKS if t.name == body.task]
        if not selected:
            raise HTTPException(404, f"unknown task: {body.task}")

    provider_name, llm_harness_fn = _build_llm_harness(
        body.provider, body.model, body.api_key, body.base_url)

    # Reset usage accumulator before the run
    from kairos.bench import real_eval as _re
    _re.reset_usage()

    started = time.time()
    results: list[HarnessResult] = []

    if method == "difflib":
        # difflib: use harness_eval.run_task with the LLM harness
        for task in selected:
            logger.info(f"harness/difflib {task.name} via {provider_name}")
            work = Path(_tempfile.mkdtemp(prefix=f"hf_{task.name}_"))
            try:
                for path, content in task.repo_files.items():
                    full = work / path
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_text(content, encoding="utf-8")
                r = await run_task_fn(task, llm_harness_fn)
                results.append(r)
            finally:
                _shutil.rmtree(work, ignore_errors=True)
    else:
        # pytest / semantic: each has its own run_one_task that handles
        # the work_dir + apply_diff + scoring internally.
        for task in selected:
            logger.info(f"harness/{method} {task.name} via {provider_name}")
            r = await _run_one_task(task, llm_harness_fn)
            results.append(r)

    report = HarnessReport(results=results, total_s=time.time() - started)
    out = report.to_dict()
    out["provider"] = provider_name
    out["model"] = body.model or _default_model(body.provider)
    out["method"] = method
    # usage + estimated cost
    usage_snapshot = dict(_re.USAGE)
    cost = _re.estimate_cost(out["model"])
    out["usage"] = usage_snapshot
    out["cost_usd"] = round(cost, 4)

    if body.persist:
        _record_leaderboard(
            provider_name, out["model"], method, out,
            usage=usage_snapshot, cost_usd=cost)

    return out


def _default_model(provider: str) -> str:
    return {
        "minimax": "MiniMax-Text-01",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-sonnet-latest",
    }.get(provider.lower(), "unknown")


def _build_llm_harness(provider: str, model: str, api_key: str,
                       base_url: str):
    """Return (provider_name, llm_harness_fn). The harness function takes
    (task, work_dir) and returns the predicted diff string."""
    p = provider.lower().strip()
    from kairos.bench.real_eval import llm_harness_for as _llm_for
    if p in ("minimax", "minimaxi"):
        # real_eval uses module-level env / file API key
        fn = _llm_for("minimax", model=model, api_key=api_key,
                      base_url=base_url)
        return "minimax", fn
    if p in ("openai", "anthropic"):
        fn = _llm_for(p, model=model, api_key=api_key, base_url=base_url)
        return p, fn
    raise HTTPException(400, f"unknown provider: {provider}")


@router.get("/harness/leaderboard")
async def harness_leaderboard(method: str = "", limit: int = 50):
    """Return the per-model harness leaderboard (R38.6.4).

    Each entry is one (provider, model, method) run. Optional ``method``
    filter, returns the latest ``limit`` entries (default 50, max 200).
    """
    entries = _load_leaderboard()
    if method:
        entries = [e for e in entries if e.get("method") == method]
    # Sort by ts descending, take last ``limit``
    entries = sorted(entries, key=lambda e: e.get("ts", 0), reverse=True)
    entries = entries[:max(1, min(200, limit))]

    # Build a "best per (provider, model, method)" summary
    best: dict[tuple, dict] = {}
    for e in entries:
        key = (e.get("provider"), e.get("model"), e.get("method"))
        cur = best.get(key)
        if cur is None or e.get("pass_rate", 0) > cur.get("pass_rate", 0):
            best[key] = e
    summary = sorted(best.values(),
                     key=lambda e: (e.get("method", ""), -e.get("pass_rate", 0)))

    return {
        "count": len(entries),
        "entries": entries,
        "best": summary,
        "path": str(_HARNESS_LEADERBOARD_PATH),
    }


class CompareModel(BaseModel):
    provider: str
    model: str = ""  # default per provider
    api_key: str = ""
    base_url: str = ""


class CompareBody(BaseModel):
    task: str = ""  # empty = all 10
    method: str = "semantic"
    models: list[CompareModel]
    persist: bool = True


@router.post("/harness/compare")
async def harness_compare(body: CompareBody):
    """Run the harness against multiple (provider, model) pairs and
    return a side-by-side comparison. Records each run to the
    leaderboard by default.

    Useful for "which model should I use" decisions: feed it a list
    of candidate models, get back a ranked table of pass_rate /
    mean_score / cost / total time.
    """
    from kairos.bench.harness_eval import (
        TASKS as _TASKS, HarnessReport, HarnessResult,
    )
    import asyncio as _asyncio
    import shutil as _shutil
    import tempfile as _tempfile

    method = body.method.lower().strip()
    if method not in {"difflib", "pytest", "semantic"}:
        raise HTTPException(
            400, f"unknown method: {method} (use difflib|pytest|semantic)")
    if not body.models:
        raise HTTPException(400, "models list is empty")

    selected = _TASKS
    if body.task:
        selected = [t for t in _TASKS if t.name == body.task]
        if not selected:
            raise HTTPException(404, f"unknown task: {body.task}")

    rows: list[dict] = []
    overall_start = time.time()
    for cm in body.models:
        from kairos.bench import real_eval as _re
        _re.reset_usage()
        provider_name, harness_fn = _build_llm_harness(
            cm.provider, cm.model, cm.api_key, cm.base_url)
        model_name = cm.model or _default_model(cm.provider)
        started = time.time()
        results: list[HarnessResult] = []
        if method == "difflib":
            from kairos.bench.harness_eval import run_task as _run_task
            for task in selected:
                work = Path(_tempfile.mkdtemp(prefix=f"cmp_{task.name}_"))
                try:
                    for path, content in task.repo_files.items():
                        full = work / path
                        full.parent.mkdir(parents=True, exist_ok=True)
                        full.write_text(content, encoding="utf-8")
                    r = await _run_task(task, harness_fn)
                    results.append(r)
                finally:
                    _shutil.rmtree(work, ignore_errors=True)
        elif method == "pytest":
            from kairos.bench.harness_pytest_eval import run_one_task
            for task in selected:
                r = await run_one_task(task, harness_fn)
                results.append(r)
        else:
            from kairos.bench.harness_semantic_eval import run_one_task
            for task in selected:
                r = await run_one_task(task, harness_fn)
                results.append(r)
        report = HarnessReport(results=results, total_s=time.time() - started)
        usage = dict(_re.USAGE)
        cost = _re.estimate_cost(model_name)
        out = report.to_dict()
        out.update({
            "provider": provider_name,
            "model": model_name,
            "method": method,
            "usage": usage,
            "cost_usd": round(cost, 4),
        })
        if body.persist:
            _record_leaderboard(
                provider_name, model_name, method, out,
                usage=usage, cost_usd=cost)
        rows.append(out)

    # Rank by pass_rate desc, then by mean_score desc
    rows.sort(key=lambda r: (-r.get("pass_rate", 0), -r.get("mean_score", 0)))
    return {
        "method": method,
        "task_count": len(selected),
        "total_s": round(time.time() - overall_start, 2),
        "rows": rows,
        "winner": rows[0] if rows else None,
    }
