"""Asking the user, and waiting for the answer.

The gate shipped with one honest gap: it could decide, but it could not *ask*.
`kairos/approval.py`'s ladder returns ASK for actions that are neither clearly
safe nor clearly forbidden, and with no channel to ask through, an ASK had to be
resolved by policy — allowed (useful, but then the ladder is only advice) or
refused (safe, but the first file write deadlocks the run, which is exactly why
strict mode is opt-in). `kairos/sentinel.py` says as much in its own docstring:
"ASK mean deny once an approval channel exists".

This is that channel. A question becomes a record, the record is published on the
message bus so a UI can render it, and the tool call **waits** — with a timeout —
for an answer. Approved, and the caller may record a standing rule so the user is
asked once instead of every round. Denied or timed out, and the gate refuses,
which is the behaviour it already had.

The timeout is not a formality: a request that nobody answers has to resolve, or
a background run parks forever on a question no one will see.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How long a tool call waits for an answer before the gate falls back to
# refusing. Long enough to notice a banner, short enough not to hang a run.
DEFAULT_TIMEOUT_S = 120.0

# Keep the resolved requests around for the UI, bounded: this is a transcript of
# decisions, not a log file.
HISTORY_LIMIT = 200


@dataclass
class ApprovalRequest:
    """One question: may this tool call happen?"""

    id: str
    tool: str
    resource: str = ""
    reason: str = ""
    project_id: str = ""
    created_at: float = field(default_factory=time.time)
    timeout_s: float = DEFAULT_TIMEOUT_S
    # pending | allowed | denied | timeout | cancelled
    status: str = "pending"
    remember: bool = False
    answered_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ApprovalChannel:
    """Pending questions plus the futures their askers are waiting on."""

    def __init__(self, message_bus: Any = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._bus = message_bus
        self._timeout_s = float(timeout_s)
        self._pending: Dict[str, ApprovalRequest] = {}
        self._waiters: Dict[str, "asyncio.Future[bool]"] = {}
        self._history: List[Dict[str, Any]] = []

    def attach_bus(self, bus: Any) -> None:
        """Wire the message bus after construction (the app builds it later)."""
        self._bus = bus

    # -- observation ------------------------------------------------------

    def pending(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._pending.values()]

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        req = self._pending.get(request_id)
        return req.to_dict() if req else None

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._history[-max(1, int(limit)):]

    def stats(self) -> Dict[str, int]:
        out = {"pending": len(self._pending), "allowed": 0, "denied": 0,
               "timeout": 0, "cancelled": 0}
        for entry in self._history:
            status = str(entry.get("status") or "")
            if status in out:
                out[status] += 1
        return out

    # -- the question -----------------------------------------------------

    async def request(self, *, tool: str, resource: str = "", reason: str = "",
                      project_id: str = "",
                      timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """Publish a question and wait for the answer (or the timeout).

        Returns ``{"id", "status", "allow", "remember", "reason"}``. Never
        raises for a denial or a timeout: an unanswered question is an answer,
        and it is "no".
        """
        req = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            tool=tool,
            resource=resource,
            reason=reason,
            project_id=project_id,
            timeout_s=float(timeout_s or self._timeout_s),
        )
        loop = asyncio.get_running_loop()
        waiter: "asyncio.Future[bool]" = loop.create_future()
        self._pending[req.id] = req
        self._waiters[req.id] = waiter

        await self._publish("approval.requested", req,
                            "Waiting for approval: " + req.tool)
        try:
            await asyncio.wait_for(waiter, timeout=req.timeout_s)
            req.status = "allowed" if waiter.result() else "denied"
            req.remember = bool(getattr(waiter, "_kairos_remember", False))
        except asyncio.TimeoutError:
            req.status = "timeout"
        except asyncio.CancelledError:
            req.status = "cancelled"
            raise
        finally:
            req.answered_at = time.time()
            self._waiters.pop(req.id, None)
            self._pending.pop(req.id, None)
            self._history.append(req.to_dict())
            if len(self._history) > HISTORY_LIMIT:
                del self._history[:-HISTORY_LIMIT]

        await self._publish("approval.resolved", req,
                            "Approval " + req.status + ": " + req.tool)
        return {"id": req.id, "status": req.status,
                "allow": req.status == "allowed", "remember": req.remember,
                "reason": req.reason}

    def resolve(self, request_id: str, allow: bool, *,
                remember: bool = False) -> bool:
        """Answer a pending question. False when the id is unknown or stale."""
        waiter = self._waiters.get(request_id)
        if waiter is None or waiter.done():
            return False
        # Stash the "remember" flag on the future: it is part of the answer, and
        # it belongs to the same single resolution.
        setattr(waiter, "_kairos_remember", bool(remember))
        waiter.set_result(bool(allow))
        return True

    def cancel_all(self, reason: str = "shutting down") -> int:
        """Resolve every pending question as denied (used on shutdown)."""
        n = 0
        for request_id, waiter in list(self._waiters.items()):
            if not waiter.done():
                waiter.set_result(False)
                n += 1
        for req in self._pending.values():
            req.reason = (req.reason + " | " + reason).strip(" |")
        return n

    # -- publishing -------------------------------------------------------

    async def _publish(self, topic: str, req: ApprovalRequest,
                       content: str) -> None:
        if self._bus is None:
            return
        try:
            from kairos.core.message_bus import Message
            await self._bus.publish(Message(
                sender="sentinel", topic=topic, content=content,
                msg_type="text",
                metadata={"request_id": req.id, "tool": req.tool,
                          "resource": req.resource, "reason": req.reason,
                          "project_id": req.project_id, "status": req.status,
                          "timeout_s": req.timeout_s},
            ))
        except Exception as e:  # noqa: BLE001
            # A bus that cannot carry the question must not lose it: the UI
            # polls /api/approvals as well, and the timeout still applies.
            logger.debug("approval event publish failed: %s", e)


# ---------------------------------------------------------------------------
# Process-wide channel (the app wires one at startup, like the registries)
# ---------------------------------------------------------------------------

_channel: Optional[ApprovalChannel] = None


def set_channel(channel: Optional[ApprovalChannel]) -> None:
    global _channel
    _channel = channel


def get_channel() -> Optional[ApprovalChannel]:
    return _channel
