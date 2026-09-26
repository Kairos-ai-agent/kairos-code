"""Approvals API — the gate's question, and the answer to it.

`kairos/approvals.py` holds the channel; `kairos/sentinel.py`'s
`authorize_async` publishes into it when the permission ladder returns ASK.
These routes are the other half: the list a UI renders, and the one call that
answers. Without them the question would be published and nobody could reply —
which is the same as not asking.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class Answer(BaseModel):
    allow: bool
    remember: bool = False


def _channel():
    from kairos import approvals
    channel = approvals.get_channel()
    if channel is None:
        # Not an error the UI should shout about: a process can legitimately run
        # without a channel (a CLI run, a test), and then the gate simply keeps
        # deciding on its own.
        raise HTTPException(503, "no approval channel is wired in this process")
    return channel


@router.get("")
async def list_approvals(limit: int = 50):
    """Pending questions first: they are the ones with a deadline."""
    from kairos import approvals

    channel = approvals.get_channel()
    if channel is None:
        return {"wired": False, "pending": [], "history": [], "stats": {}}
    return {
        "wired": True,
        "pending": channel.pending(),
        "history": channel.history(max(1, int(limit))),
        "stats": channel.stats(),
    }


@router.post("/{request_id}")
async def answer(request_id: str, body: Answer):
    """Allow or deny one pending request. One question, one answer."""
    channel = _channel()
    if not channel.resolve(request_id, body.allow, remember=body.remember):
        raise HTTPException(404, "no pending request with that id")
    return {"ok": True, "request_id": request_id, "allow": body.allow,
            "remember": body.remember}
