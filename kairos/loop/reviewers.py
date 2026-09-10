"""Single-Reviewer execution, verdict parsing, and legacy merge support."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, Iterable

# R38.6.4 packaging: was 'from kairos.agents.base import AgentTask' — replaced with __getattr__ lazy load
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

def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    return bool(value)


def _coerce_line(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _normalize_bug_verdict(data: Any) -> Dict[str, Any] | None:
    """Map the R38.7 simple Reviewer shape onto the internal verdict.

    The simplified Reviewer answers one question only — "are there bugs?" —
    with ``{"has_bugs": bool, "bugs": [{file, line, description, fix}],
    "summary": str}``. The loop, the UI and the memory store all speak the
    older ``{approve, score, issues, summary}`` shape, so translate instead
    of ripping that plumbing out:

    - no bugs  -> approve=True,  score=100 (clears the 85 approval bar)
    - N bugs   -> approve=False, score=max(0, 100 - 20N)

    Returns None for anything that is not the simple shape, so the legacy
    rubric verdict still parses (old sessions, old replays, existing tests).
    """
    if not isinstance(data, dict):
        return None
    if "bugs" not in data and "has_bugs" not in data:
        return None

    raw_bugs = data.get("bugs")
    raw_bugs = raw_bugs if isinstance(raw_bugs, list) else []
    issues: list = []
    for raw in raw_bugs:
        if not isinstance(raw, dict):
            continue
        description = str(
            raw.get("description") or raw.get("summary") or "Bug"
        ).strip()[:2000]
        issues.append({
            "category": "bug",
            "severity": "BUG",
            "file": str(raw.get("file") or ""),
            "line": _coerce_line(raw.get("line")),
            "description": description,
            "fix_instruction": str(
                raw.get("fix") or raw.get("fix_instruction")
                or "Fix this bug.").strip()[:2000],
        })

    has_bugs = bool(issues) or _as_bool(data.get("has_bugs"))
    if has_bugs and not issues:
        # "has_bugs: true" with an empty list — keep the rejection (the Coder
        # still gets told to look) instead of approving a round the Reviewer
        # refused to approve.
        issues.append({
            "category": "bug",
            "severity": "BUG",
            "file": "",
            "line": 0,
            "description": "Reviewer reported bugs without listing them.",
            "fix_instruction": (
                "Re-check the changes for the bug the Reviewer saw and fix it."),
        })

    summary = str(data.get("summary") or "").strip()
    if not summary:
        summary = (f"{len(issues)} bug(s) found." if has_bugs
                   else "No bugs found.")
    return {
        "approve": not has_bugs,
        "score": 100 if not has_bugs else max(0, 100 - 20 * len(issues)),
        "issues": issues,
        "summary": summary[:4000],
        "_simple_bug_review": True,
        "_confidence": 0.7,
    }


def _normalize_verdict(data: Any) -> Dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    # R38.7: the bug-only Reviewer shape first (current default).
    simple = _normalize_bug_verdict(data)
    if simple is not None:
        return simple
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
    # Test evidence: the Reviewer reports whether it actually ran the
    # project's test suite. Must survive normalization, otherwise the
    # loop-runner calibration (require_test_evidence) would treat every
    # verdict as unverified and never allow approval.
    evidence = data.get("tests_evidence")
    if isinstance(evidence, dict):
        verdict["tests_evidence"] = evidence
    # Confidence calibration: Reviewer may emit _confidence (0.0-1.0)
    # reflecting how certain they are about the verdict. Clamp to the
    # valid range; absent value defaults to 0.5 (medium-low).
    raw_conf = data.get("_confidence")
    try:
        conf = float(raw_conf)
    except (TypeError, ValueError):
        conf = 0.5
    verdict["_confidence"] = max(0.0, min(1.0, conf))
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
    # R38.6.4 packaging: AgentTask is lazy-loaded via module __getattr__,
    # which does not fire for a bare-name lookup inside this function —
    # import locally so the name resolves at runtime.
    from kairos.agents.base import AgentTask
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



# R38.6.4 packaging: lazy import so PyInstaller onefile
# can resolve this module (eager top-level imports trip
# the bootloader when --collect-submodules misses the
# symbol).
def __getattr__(name):
    if name in ['AgentTask']:
        import importlib as _il, kairos.agents.base as _m
        return getattr(_m, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
