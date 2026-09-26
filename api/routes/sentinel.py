"""The gate's own API: what it decided, and how to grant a standing exception.

Muse's Sentinel surfaces an audit trail and an approval surface in the product;
until this app grows a channel that can ask and wait, the honest equivalent is a
read-only trail plus an explicit "allow this, from now on" endpoint. Refusals
point here.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sentinel", tags=["sentinel"])


@router.get("/status")
async def status() -> Dict[str, Any]:
    """Whether the gate is on, and what it is enforcing right now."""
    from kairos.sentinel import allow_file, get_sentinel

    sentinel = get_sentinel()
    return {
        "enabled": sentinel.enabled,
        "strict": sentinel.strict,
        "mode": sentinel.mode.value,
        "audit_dir": str(sentinel.audit.directory),
        "allow_file": str(allow_file()),
        "policy": sentinel.policy.to_dict(),
        "always_refused": [
            "private keys and cloud credentials (.ssh/id_*, .aws/credentials,"
            " .netrc, .git-credentials)",
            "tainted egress: once the run has read untrusted content, network"
            " tools, git push and curl/wget/ssh in the shell are refused",
            "tainted reads of .env / secrets files",
        ],
    }


@router.get("/audit")
async def audit(
    limit: int = Query(default=200, ge=1, le=2000),
    decision: Optional[str] = Query(default=None, description="allow | deny"),
) -> Dict[str, Any]:
    """Recent rulings, newest first, with a tally of what was refused."""
    from kairos.sentinel import get_sentinel

    entries: List[Dict[str, Any]] = get_sentinel().audit.read(limit=limit)
    if decision:
        entries = [e for e in entries if e.get("decision") == decision]
    counts = {"allow": 0, "deny": 0}
    for entry in entries:
        key = entry.get("decision")
        if key in counts:
            counts[key] += 1
    return {
        "entries": entries,
        "counts": counts,
        "total": len(entries),
        "denied": [e for e in entries if e.get("decision") == "deny"][:20],
    }


class AllowRequest(BaseModel):
    """A standing rule the user grants, so the gate stops refusing."""

    tool: str
    pattern: str = "*"
    decision: str = "allow"


@router.post("/allow")
async def allow(request: AllowRequest) -> Dict[str, Any]:
    """Record a standing rule and reload the policy immediately."""
    from kairos.sentinel import get_sentinel, write_allow_rule

    if request.decision not in ("allow", "deny", "ask"):
        raise HTTPException(status_code=400, detail="decision must be allow, ask or deny")
    try:
        path = write_allow_rule(request.tool, request.pattern, request.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sentinel = get_sentinel()
    sentinel.reload()
    logger.info("sentinel: standing rule recorded in %s", path)
    return {
        "ok": True,
        "file": str(path),
        "policy": sentinel.policy.to_dict(),
    }
