"""API endpoints for the alert system (R28 dispatcher + R30 UI).

Three endpoints:

  - GET  /api/alerts/recent   — read data/alerts.jsonl, return last N
  - GET  /api/alerts/summary  — counts by severity + last_critical_at
  - POST /api/alerts/mute     — write {key, expires_at} to mutes file

Mutes are persisted at ``<KAIROS_DATA_DIR>/alerts_mutes.json`` and
applied client-side (the UI checks each alert's kind+metric against
the mutes set). Keeping mute state on the server means the UI
doesn't need its own store and multiple browser tabs stay in sync.

Why server-side mute state, not localStorage?
  - Mutes survive a browser refresh.
  - Mutes are shared between team members using the same backend.
  - "Mute this kind forever" persists across deployments.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from kairos.alerts_dispatcher import _get_history_path

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_MUTE_S = 3600        # 1 h
MAX_MUTE_S = 7 * 24 * 3600   # 7 d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mutes_path() -> Path:
    """Resolve the mute state file (separate from alert history)."""
    import os
    from kairos.alerts_dispatcher import _get_history_path
    h = _get_history_path()
    return h.parent / "alerts_mutes.json"


def _load_mutes() -> Dict[str, float]:
    p = _mutes_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_mutes(mutes: Dict[str, float]) -> None:
    p = _mutes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(mutes, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    import os
    os.replace(tmp, p)


def _is_muted(mutes: Dict[str, float], key: str) -> bool:
    """A mute is active if its expiry is in the future."""
    exp = mutes.get(key)
    if exp is None:
        return False
    return exp > time.time()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/recent")
async def recent_alerts(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT,
                       description="Max entries to return (newest first)"),
) -> Dict[str, Any]:
    """Return the most recent ``limit`` entries from alerts.jsonl."""
    from kairos.alerts_dispatcher import read_history
    p = _get_history_path()
    entries = read_history(p, limit=limit)
    return {
        "entries": entries,
        "count": len(entries),
        "history_path": str(p),
    }


@router.get("/summary")
async def alerts_summary() -> Dict[str, Any]:
    """Return severity counts + latest critical timestamp.

    Cheap: reads the same file as /recent but reduces server-side.
    """
    from kairos.alerts_dispatcher import read_history
    p = _get_history_path()
    entries = read_history(p, limit=MAX_LIMIT)

    by_sev: Dict[str, int] = {"info": 0, "warning": 0, "critical": 0}
    by_status: Dict[str, int] = {"sent": 0, "failed": 0, "skipped": 0}
    by_kind: Dict[str, int] = {}
    last_critical_at: Optional[float] = None
    for e in entries:
        sev = str(e.get("severity", "info"))
        if sev not in by_sev:
            by_sev[sev] = 0
        by_sev[sev] += 1
        st = str(e.get("status", "sent"))
        if st not in by_status:
            by_status[st] = 0
        by_status[st] += 1
        k = str(e.get("kind", "?"))
        by_kind[k] = by_kind.get(k, 0) + 1
        if sev == "critical" and (last_critical_at is None
                                  or e.get("timestamp", 0) > last_critical_at):
            last_critical_at = float(e.get("timestamp", 0))

    # Drop expired mutes from the count we report (so the UI can know
    # "2 of these mutes are stale and will be cleaned up next write")
    mutes = _load_mutes()
    now = time.time()
    active_mutes = sum(1 for exp in mutes.values() if exp > now)
    return {
        "total": len(entries),
        "by_severity": by_sev,
        "by_status": by_status,
        "by_kind": by_kind,
        "last_critical_at": last_critical_at,
        "active_mutes": active_mutes,
    }


@router.post("/mute")
async def mute_alert(
    key: str = Body(..., embed=True,
                    description="The mute key, e.g. 'cost_spike:cost_usd'"),
    duration_s: int = Body(DEFAULT_MUTE_S, embed=True, ge=1, le=MAX_MUTE_S,
                          description="How long to mute, in seconds"),
) -> Dict[str, Any]:
    """Mute an alert key for ``duration_s`` seconds."""
    if not key or ":" not in key:
        raise HTTPException(
            status_code=400,
            detail="key must be in 'kind:metric' format, e.g. 'cost_spike:cost_usd'",
        )
    mutes = _load_mutes()
    expires_at = time.time() + duration_s
    mutes[key] = expires_at
    _save_mutes(mutes)
    return {"key": key, "expires_at": expires_at, "duration_s": duration_s}


@router.delete("/mute/{key}")
async def unmute_alert(key: str) -> Dict[str, Any]:
    """Remove a mute (no-op if it doesn't exist)."""
    mutes = _load_mutes()
    had = mutes.pop(key, None)
    _save_mutes(mutes)
    return {"key": key, "removed": had is not None}


@router.get("/mutes")
async def list_mutes() -> Dict[str, Any]:
    """List all active mutes (filter out expired ones for the response)."""
    mutes = _load_mutes()
    now = time.time()
    return {
        "mutes": {k: v for k, v in mutes.items() if v > now},
        "count": sum(1 for v in mutes.values() if v > now),
    }
