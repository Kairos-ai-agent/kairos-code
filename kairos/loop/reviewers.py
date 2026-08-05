"""Single-Reviewer execution, verdict parsing, and legacy merge support."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, Iterable

from kairos.agents.base import AgentTask
from kairos.core.message_bus import Message
from kairos.loop.gates import PER_ROUND_TIMEOUT_S
from kairos.loop.prompts import build_reviewer_description

logger = logging.getLogger(__name__)

def _failure_verdict(mode: str, summary: str, description: str) -> Dict[str, Any]:
    return {
        "approve": False,
        "score": 0,
        "issues": [{
            "category": "review_infrastructure",
            "severity": "MAJOR",
            "file": "",
            "line": 0,
            "description": description,
            "fix_instruction": "Retry review with a smaller diff or a stricter JSON response.",
        }],
        "summary": summary,
        "_failure_mode": mode,
    }

def _balanced_json_objects(text: str) -> Iterable[str]:
    start = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start:index + 1]
                start = None

def _normalize_verdict(data: Any) -> Dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    if not any(key in data for key in ("approve", "score", "issues", "summary")):
        return None
    raw_score = data.get("score", 0)
    try:
        score = max(0, min(100, int(float(raw_score))))
    except (TypeError, ValueError):
        score = 0
    raw_approve = data.get("approve", False)
    approve = raw_approve is True or (
        isinstance(raw_approve, str) and raw_approve.strip().lower() == "true"
    )
    issues = []
    for raw_issue in data.get("issues") or []:
        if not isinstance(raw_issue, dict):
            continue
        issue = dict(raw_issue)
        severity = str(issue.get("severity") or "MAJOR").upper()
        if severity not in {"CRITICAL", "MAJOR", "MINOR", "SUGGESTION"}:
            severity = "MAJOR"
        issue["severity"] = severity
        issue.setdefault("category", "correctness")
        issue.setdefault("file", "")
        issue.setdefault("line", 0)
        issue.setdefault("description", "Unspecified review issue")
        issue.setdefault("fix_instruction", "Address this issue and verify the fix.")
        issues.append(issue)
    verdict = {
        "approve": approve,
        "score": score,
        "issues": issues,
        "summary": str(data.get("summary") or "")[:4000],
    }
    ask_human = data.get("ask_human")
    if isinstance(ask_human, dict):
        verdict["ask_human"] = ask_human
    failure_mode = data.get("_failure_mode")
    if failure_mode:
        verdict["_failure_mode"] = str(failure_mode)
    return verdict

def parse_review_verdict(raw: str) -> Dict[str, Any]:
    """Parse strict, fenced, or surrounding-text JSON without losing nesting."""
    text = (raw or "").strip()
    if not text or "tool call limit" in text.lower():
        return _failure_verdict(
            "tool_limit",
            "Reviewer turn limit hit before verdict.",
            "Reviewer exhausted its tool-call budget before producing a verdict.",
        )
    candidates = [text]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    )
    candidates.extend(_balanced_json_objects(text))
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            verdict = _normalize_verdict(json.loads(candidate))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if verdict is not None:
            return verdict
    return _failure_verdict(
        "parse_fail",
        "Reviewer output was not parseable JSON.",
        "Reviewer returned text that could not be parsed as the required verdict.",
    )

async def run_reviewer_round_for(
    session: Any,
    coder_result: str,
    round_no: int,
    reviewer: Any,
    precheck_hint: str = "",
) -> Dict[str, Any]:
    """Run one Reviewer agent and return a normalized verdict."""
    role = getattr(reviewer, "role", "reviewer")
    name = getattr(reviewer, "name", "Reviewer")
    task = AgentTask(
        id=uuid.uuid4().hex[:8],
        title=f"{name} round {round_no}",
        description=build_reviewer_description(
            session, coder_result, round_no, precheck_hint
        ),
        context={"round": round_no, "review_focus": getattr(session, "review_focus", [])},
    )
    await session.message_bus.publish(Message(
        sender="orchestrator",
        topic=f"loop.{role}_started",
        content=f"{name} round {round_no} starting...",
        msg_type="text",
        metadata={
            "project_id": session.project.id,
            "session_id": session.session_id,
            "round": round_no,
        },
    ))
    try:
        raw = await asyncio.wait_for(
            reviewer.run(task), timeout=PER_ROUND_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        await session.message_bus.publish(Message(
            sender="orchestrator",
            topic=f"loop.{role}_timeout",
            content=f"Round {round_no} exceeded {PER_ROUND_TIMEOUT_S:.0f}s",
            msg_type="error",
            metadata={
                "project_id": session.project.id,
                "session_id": session.session_id,
                "round": round_no,
            },
        ))
        return _failure_verdict(
            "infra_fail", "Reviewer timed out", "Reviewer timed out before grading this round."
        )
    except Exception as error:
        logger.exception("reviewer %s crashed", role)
        return _failure_verdict(
            "infra_fail", f"Reviewer crashed: {error}", "Reviewer execution crashed."
        )
    return parse_review_verdict(raw or "")

async def run_reviewer_round(
    session: Any,
    coder_result: str,
    round_no: int,
    precheck_hint: str = "",
) -> Dict[str, Any]:
    """Run the project's one automatic Reviewer."""
    return await run_reviewer_round_for(
        session, coder_result, round_no, session.reviewer, precheck_hint
    )

async def run_reviewers_parallel(
    session: Any, coder_result: str, round_no: int
) -> Dict[str, Any]:
    """Legacy explicit multi-review merge; production orchestration stays two-agent."""
    from kairos.agents.roles import DEFAULT_SPECIALIST_WEIGHTS
    reviewers = [("reviewer", session.reviewer)] + [
        (getattr(agent, "role", "reviewer"), agent)
        for agent in (getattr(session, "specialist_reviewers", None) or [])
    ]
    async def run_one(role: str, agent: Any):
        verdict = await run_reviewer_round_for(session, coder_result, round_no, agent)
        verdict["_source"] = role
        return role, verdict
    results = await asyncio.gather(*(run_one(role, agent) for role, agent in reviewers))
    weighted_score = 0.0
    total_weight = 0.0
    all_issues = []
    summaries = []
    approve_all = True
    any_critical = False
    per_reviewer = {}
    for role, verdict in results:
        per_reviewer[role] = verdict
        weight = DEFAULT_SPECIALIST_WEIGHTS.get(role, 0.1)
        weighted_score += int(verdict.get("score", 0) or 0) * weight
        total_weight += weight
        approve_all = approve_all and bool(verdict.get("approve"))
        summaries.append(f"[{role}] {verdict.get('summary', '')[:200]}")
        for raw_issue in verdict.get("issues") or []:
            issue = dict(raw_issue)
            issue["_source_reviewer"] = role
            any_critical = any_critical or issue.get("severity") == "CRITICAL"
            all_issues.append(issue)
    return {
        "approve": approve_all and not any_critical,
        "score": int(weighted_score / total_weight) if total_weight else 0,
        "issues": all_issues,
        "summary": " | ".join(summaries),
        "_per_reviewer": per_reviewer,
    }
