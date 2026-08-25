"""HTTP API for the trace viewer.

Endpoints (mounted under /api/projects/{project_id}/traces by app.py):

    GET  /traces                    — list all session traces for a project
    GET  /traces/{session_id}       — full trace for one session
    GET  /traces/{session_id}/turns — turn-by-turn summary (lighter than full)
    GET  /traces/{session_id}/tools — tool call summary

The trace data is read from the JSONL files written by kairos.tracing.
The endpoint is read-only; the agent's loop is the writer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from kairos.tracing import (
    EventKind,
    Trace,
    TraceEvent,
    list_sessions,
    load_trace,
    trace_file_path,
)

router = APIRouter()


@router.get("/{project_id}/traces")
async def list_project_traces(project_id: str):
    """List all session traces for the given project.

    Returns a list of {session_id, event_count, size_bytes, path}.
    The path is the on-disk JSONL file (relative to cwd).
    """
    sessions = list_sessions(project_id)
    return {"project_id": project_id, "sessions": sessions}


@router.get("/{project_id}/traces/{session_id}")
async def get_full_trace(project_id: str, session_id: str,
                         include_messages: bool = Query(True),
                         max_content_chars: int = Query(0, ge=0)):
    """Return the full trace for a session.

    `max_content_chars`: if > 0, truncate the `content` and `output`
    fields of each event to that many characters. Useful for
    serving traces to a UI that doesn't want to render 100KB
    tool outputs.
    """
    trace = load_trace(project_id, session_id)
    if trace is None:
        raise HTTPException(status_code=404,
                            detail=f"trace not found: {project_id}/{session_id}")
    return _trace_to_response(trace, include_messages=include_messages,
                              max_content_chars=max_content_chars)


@router.get("/{project_id}/traces/{session_id}/turns")
async def get_trace_turns(project_id: str, session_id: str):
    """Return a turn-by-turn summary (one row per turn).

    Each row includes: turn number, event count, has tool calls,
    prompt/completion tokens, and the first 200 chars of the
    completion text.
    """
    trace = load_trace(project_id, session_id)
    if trace is None:
        raise HTTPException(status_code=404,
                            detail=f"trace not found: {project_id}/{session_id}")
    rows: List[Dict[str, Any]] = []
    for turn in trace.turns():
        events = trace.events_for_turn(turn)
        first_completion = next(
            (e for e in events if e.kind == EventKind.COMPLETION), None)
        tool_count = sum(1 for e in events if e.kind == EventKind.TOOL_CALL)
        prompt_tokens = 0
        completion_tokens = 0
        if first_completion:
            usage = first_completion.payload.get("usage", {}) or {}
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        snippet = ""
        if first_completion:
            content = first_completion.payload.get("content", "") or ""
            snippet = content[:200] + ("…" if len(content) > 200 else "")
        rows.append({
            "turn": turn,
            "event_count": len(events),
            "tool_call_count": tool_count,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "completion_snippet": snippet,
        })
    return {
        "project_id": project_id,
        "session_id": session_id,
        "turn_count": len(rows),
        "turns": rows,
    }


@router.get("/{project_id}/traces/{session_id}/tools")
async def get_trace_tools(project_id: str, session_id: str):
    """Return per-tool summary across the whole trace.

    Each row: tool name, call count, success count, failure count,
    total duration_ms. Sorted by call count descending.
    """
    trace = load_trace(project_id, session_id)
    if trace is None:
        raise HTTPException(status_code=404,
                            detail=f"trace not found: {project_id}/{session_id}")
    by_tool: Dict[str, Dict[str, int]] = {}
    for ev in trace.events:
        if ev.kind == EventKind.TOOL_RESULT:
            name = ev.payload.get("name", "")
            row = by_tool.setdefault(name,
                                     {"calls": 0, "success": 0, "failed": 0,
                                      "duration_ms": 0})
            row["calls"] += 1
            if ev.payload.get("success", True):
                row["success"] += 1
            else:
                row["failed"] += 1
            row["duration_ms"] += int(ev.payload.get("duration_ms", 0) or 0)
    rows = sorted(by_tool.items(), key=lambda kv: -kv[1]["calls"])
    return {
        "project_id": project_id,
        "session_id": session_id,
        "tools": [{"name": name, **stats} for name, stats in rows],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trace_to_response(trace: Trace,
                       include_messages: bool,
                       max_content_chars: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "project_id": trace.project_id,
        "session_id": trace.session_id,
        "agent_id": trace.agent_id,
        "started_at": trace.started_at,
        "finished_at": trace.finished_at,
        "turn_count": len(trace.turns()),
        "event_count": len(trace.events),
        "tokens": trace.total_tokens(),
        "events": [],
    }
    events_payload: List[Dict[str, Any]] = []
    for ev in trace.events:
        d = ev.to_dict()
        # Optionally drop the full message list to save bandwidth.
        if not include_messages and ev.kind == EventKind.PROMPT:
            d["payload"] = {
                "message_count": len(ev.payload.get("messages", []) or []),
            }
        # Optionally truncate long content / output.
        if max_content_chars > 0:
            payload = d.get("payload", {}) or {}
            for key in ("content", "output", "summary", "error"):
                v = payload.get(key)
                if isinstance(v, str) and len(v) > max_content_chars:
                    payload[key] = (v[:max_content_chars]
                                    + f"… [truncated, total {len(v)} chars]")
        events_payload.append(d)
    out["events"] = events_payload
    return out
