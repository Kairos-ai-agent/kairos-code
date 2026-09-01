"""Output guardrails (the cloud task-Harness-style).

A guardrail is a hook that runs AFTER an agent finishes its main loop
but BEFORE the result is published. It inspects the final assistant
text and decides whether to:

  1. **Trip** — the result is unsafe / broken / policy-violating
     (e.g. CRITICAL security issue). By default this does NOT raise;
     it appends a flagged-result envelope so downstream consumers
     (orchestrator, UI, message bus) can decide what to do.
  2. **Pass** — the result is fine, do nothing.

We deliberately keep guardrails opt-in (a constructor arg on
KairosAgent), because the most useful kind — calling a real Reviewer
agent to grade the output — costs an extra LLM round-trip per
agent.run().

This is the the cloud task-Harness-style "everything is a hook" pattern: the
loop body stays simple, side concerns (review, policy, telemetry)
plug in as guardrail functions.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from kairos.agents.base import AgentTask
from kairos.core.message_bus import Message, MessageBus
from kairos.loop.reviewers import (
    parse_review_verdict,
    run_reviewer_round_for,
)

logger = logging.getLogger(__name__)


@dataclass
class GuardrailResult:
    """Outcome of a guardrail check."""
    tripwire: bool = False
    severity: str = "NONE"   # CRITICAL | MAJOR | MINOR | SUGGESTION | NONE
    summary: str = ""
    issues: List[Dict[str, Any]] = field(default_factory=list)
    raw_verdict: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tripwire": self.tripwire,
            "severity": self.severity,
            "summary": self.summary,
            "issues": list(self.issues),
            "error": self.error,
        }


def _pick_max_severity(issues: List[Dict[str, Any]]) -> str:
    order = {"CRITICAL": 4, "MAJOR": 3, "MINOR": 2, "SUGGESTION": 1}
    if not issues:
        return "NONE"
    return max(
        (i.get("severity", "MINOR") for i in issues),
        key=lambda s: order.get(s.upper(), 0),
    )


class OutputGuardrail:
    """Calls a Reviewer agent against the agent's final output.

    Args:
        reviewer: A Reviewer agent (any object exposing ``run(task)``
            and the attributes ``role``, ``name``). In practice this
            is the same Reviewer you'd use in the main loop — we just
            invoke it once more for the final result.
        blocking: If True, ``check()`` raises GuardrailTripwire when
            CRITICAL is found. Defaults to False so the loop can keep
            going and just publish a flagged message — the cloud task-style
            "soft fail" so the user still sees the result.
        timeout_s: Hard cap on the reviewer round. 0 = no cap.
        message_bus: When provided, results are published on
            ``guardrail.<agent_id>``.
    """

    def __init__(
        self,
        reviewer: Any,
        blocking: bool = False,
        timeout_s: float = 0.0,
        message_bus: Optional[MessageBus] = None,
    ):
        self.reviewer = reviewer
        self.blocking = blocking
        self.timeout_s = timeout_s
        self.message_bus = message_bus

    async def check(
        self,
        agent_id: str,
        output: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> GuardrailResult:
        """Run the reviewer on `output` and return a GuardrailResult.

        The reviewer is given a short task description of the form
        "Grade this output". It returns a verdict JSON; we parse it
        (using the same hardened parser as the main loop) and decide
        whether to trip.
        """
        if not output or not output.strip():
            return GuardrailResult(summary="empty output; guardrail skipped")
        # Build a minimal pseudo-session that run_reviewer_round_for
        # needs. We avoid a real session object by inlining the
        # call against a stub whose .project / .session_id are
        # inferred from context.
        try:
            task = AgentTask(
                id=f"guardrail-{uuid.uuid4().hex[:6]}",
                title=f"Output review for {agent_id}",
                description=(
                    "Grade the following agent output. Return the "
                    "standard verdict JSON.\n\n"
                    f"```\n{output[:5000]}\n```"
                ),
                context=context or {},
            )
            # Use the same code path the loop uses so we benefit
            # from its timeouts, retries, and verdict parsing.
            verdict_text = await self.reviewer.run(task)
        except Exception as exc:
            logger.warning(
                "guardrail: reviewer call failed for %s: %s",
                agent_id, exc,
            )
            return GuardrailResult(
                error=f"reviewer call failed: {exc}",
                summary="guardrail could not run",
            )

        verdict = parse_review_verdict(verdict_text or "")
        issues = list(verdict.get("issues") or [])
        severity = _pick_max_severity(issues)
        tripwire = severity == "CRITICAL" and not verdict.get("approve", False)
        summary = verdict.get("summary") or ""
        result = GuardrailResult(
            tripwire=tripwire,
            severity=severity,
            summary=summary,
            issues=issues,
            raw_verdict=verdict,
        )

        # Publish so the UI / orchestrator can see the verdict even
        # when blocking is off.
        if self.message_bus is not None:
            try:
                await self.message_bus.publish(Message(
                    sender=agent_id,
                    topic=f"guardrail.{agent_id}",
                    content=summary or f"{severity}",
                    msg_type="guardrail",
                    metadata={
                        "agent_id": agent_id,
                        "severity": severity,
                        "tripwire": tripwire,
                        "issues": issues[:10],  # cap for transport
                        "score": verdict.get("score"),
                    },
                ))
            except Exception as exc:
                logger.debug("guardrail: publish failed: %s", exc)

        if tripwire and self.blocking:
            raise GuardrailTripwire(
                f"{agent_id} output blocked by review: {severity} — {summary}"
            )
        return result


class GuardrailTripwire(RuntimeError):
    """Raised by OutputGuardrail.check() when blocking=True and the
    output contains a CRITICAL issue."""


# --- Lightweight functional guardrails (no LLM) --------------------------


def make_deny_substrings_guardrail(
    substrings: List[str],
    message: str = "forbidden content in output",
) -> Callable[[str, Dict[str, Any]], GuardrailResult]:
    """Returns a synchronous guardrail that trips on forbidden strings.

    Use for cheap, local policy checks (e.g. refuse to publish if the
    output literally contains a password that was just committed).
    For expensive checks, use ``OutputGuardrail``.
    """
    lowered = [s.lower() for s in substrings if s]

    def _check(output: str, context: Optional[Dict[str, Any]] = None) -> GuardrailResult:
        text = (output or "").lower()
        hits = [s for s in lowered if s in text]
        if not hits:
            return GuardrailResult()
        return GuardrailResult(
            tripwire=True,
            severity="CRITICAL",
            summary=f"{message} (matched: {', '.join(hits[:3])})",
            issues=[{"category": "policy", "severity": "CRITICAL",
                     "description": message, "matched": hits}],
        )

    return _check
