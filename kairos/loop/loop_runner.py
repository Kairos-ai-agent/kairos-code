"""Main Coder <-> Reviewer loop orchestrator.

Holds run_loop + the inner Coder round + auto-checkpoint helper.
The heavy lifting (gates, cross-loop memory, prompts, reviewers)
lives in dedicated submodules; this module is just the orchestrator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from kairos.agents.base import AgentTask
from kairos.core.message_bus import Message
from kairos.loop.cross_loop import (
    CODER_TEMPERATURE_START,
    coder_temperature_for_round,
    load_history_digest,
)
from kairos.loop.gates import (
    APPROVE_SCORE_THRESHOLD,
    COST_TOKEN_CAP,
    INFRA_FAILURE_LIMIT,
    LOOP_SAFETY_CAP,
    NO_PROGRESS_LIMIT,
    PER_ROUND_TIMEOUT_S,
    STAGNATION_TOLERANCE,
    STAGNATION_WINDOW,
    issues_signature,
)
from kairos.loop.plan_mode import is_plan_dirty, sanitize_plan_text
from kairos.loop.prompts import build_next_prompt
from kairos.loop.reviewers import run_reviewer_round

logger = logging.getLogger(__name__)
async def _run_coder_round(session, requirement, round_no, plan_mode=False):
    bus = session.message_bus
    coder = session.coder
    target_temp = CODER_TEMPERATURE_START if plan_mode else coder_temperature_for_round(round_no)
    if hasattr(coder, "set_temperature"):
        try:
            coder.set_temperature(target_temp)
        except Exception:
            logger.debug("set_temperature failed", exc_info=True)
    task = AgentTask(
        id=uuid.uuid4().hex[:8],
        title=("Plan" if plan_mode else f"Coder round {round_no}"),
        description=requirement,
        context={"project_id": session.project.id, "round": round_no,
                  "total_rounds_so_far": len(session.history),
                  "plan_mode": plan_mode},
    )
    await bus.publish(Message(
        sender="orchestrator",
        topic="loop.plan_started" if plan_mode else "loop.coder_started",
        content=("Coder drafting plan..." if plan_mode else f"Coder round {round_no} starting..."),
        msg_type="text",
        metadata={"project_id": session.project.id,
                  "session_id": session.session_id, "round": round_no},
    ))

    async def _call_once(prompt_extra=""):
        if prompt_extra:
            local_task = AgentTask(
                id=task.id, title=task.title,
                description=task.description + "\n\n" + prompt_extra,
                context=task.context,
            )
            try:
                return await asyncio.wait_for(coder.run(local_task, plan_mode=plan_mode), timeout=PER_ROUND_TIMEOUT_S) or ""
            except asyncio.TimeoutError:
                return ""
        try:
            return await asyncio.wait_for(coder.run(task, plan_mode=plan_mode), timeout=PER_ROUND_TIMEOUT_S) or ""
        except asyncio.TimeoutError:
            return ""

    result = await _call_once()
    if plan_mode:
        if is_plan_dirty(result):
            await bus.publish(Message(
                sender="orchestrator", topic="loop.plan_retry",
                content="Plan output looked like tool-call residue; retrying with a stricter prompt.",
                msg_type="warning",
                metadata={"project_id": session.project.id,
                          "session_id": session.session_id, "round": round_no},
            ))
            retry = await _call_once(
                "STRICT REMINDER: Respond with plain text ONLY. Do NOT emit tool_call / function_call / JSON block / control tokens."
            )
            if retry and not is_plan_dirty(retry):
                result = retry
            elif retry:
                result = sanitize_plan_text(retry) + "\n\n" + sanitize_plan_text(result)
        else:
            result = sanitize_plan_text(result)
    return result
def _auto_checkpoint(session, round_no, score, approved, summary):
    try:
        from kairos.tools.checkpoint import checkpoint_round
        workspace = Path(getattr(session.project, "work_dir", None) or getattr(session.project, "workspace", ""))
        if not workspace:
            return None
        return checkpoint_round(Path(workspace), round_no, score, summary[:200], approved)
    except Exception:
        logger.debug("auto-checkpoint failed", exc_info=True)
        return None
async def _wait_for_plan_decision(session, round_no, bus):
    if not session.plan_pending or session.plan_event is None:
        return "proceed"
    try:
        await asyncio.wait_for(session.plan_event.wait(), timeout=600.0)
    except asyncio.TimeoutError:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.plan_timeout",
            content="Plan approval timed out",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id, "round": round_no},
        ))
        return "timeout"
    if session.plan_decision == "reject":
        await bus.publish(Message(
            sender="orchestrator", topic="loop.rejected",
            content="Plan rejected by user.",
            msg_type="result",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        return "reject"
    await bus.publish(Message(
        sender="orchestrator", topic="loop.approved",
        content="Plan approved by user.",
        msg_type="result",
        metadata={"project_id": session.project.id,
                  "session_id": session.session_id},
    ))
    session.plan_completed = True
    return "proceed"
async def _run_precheck(session, workspace, round_no, bus):
    try:
        from kairos.loop.precheck import (
            pre_check_workspace, format_precheck_for_prompt,
            extract_known_fixes, format_self_debug_hint,
        )
    except Exception:
        logger.debug("precheck imports failed", exc_info=True)
        return "", []
    if not workspace or not workspace.exists():
        return "", []
    changed = []
    try:
        import subprocess
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(workspace), capture_output=True, text=True, timeout=10, check=False,
        )
        if proc.returncode == 0:
            changed = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    precheck_hint = ""
    precheck_fixable: List[Dict] = []
    try:
        precheck_result = await pre_check_workspace(workspace, changed)
        precheck_hint = format_precheck_for_prompt(precheck_result)
        if precheck_result.get("has_failures"):
            test_stderr = ""
            if precheck_result.get("tests"):
                test_stderr = (precheck_result["tests"].get("stderr", "") + precheck_result["tests"].get("stdout", ""))
            precheck_fixable = extract_known_fixes(test_stderr)
            if precheck_fixable:
                precheck_hint = precheck_hint + "\n" + format_self_debug_hint(precheck_fixable)
                await bus.publish(Message(
                    sender="orchestrator", topic="loop.precheck_fixable",
                    content=json.dumps(precheck_fixable, ensure_ascii=False),
                    msg_type="warning",
                    metadata={"project_id": session.project.id,
                              "session_id": session.session_id, "round": round_no},
                ))
        if precheck_hint:
            await bus.publish(Message(
                sender="orchestrator", topic="loop.precheck_failed",
                content=precheck_result.get("summary", "") or precheck_hint[:500],
                msg_type="warning",
                metadata={"project_id": session.project.id,
                          "session_id": session.session_id, "round": round_no},
            ))
    except Exception:
        logger.debug("precheck failed (non-fatal)", exc_info=True)
    return precheck_hint, precheck_fixable
def _update_progress(session, review):
    session.last_score = review.get("score", 0)
    session.last_approve = review.get("approve", False)
    infra_failure = review.get("_failure_mode") in ("infra_fail", "tool_limit", "parse_fail")
    sig = issues_signature(review.get("issues") or [])
    if infra_failure:
        session.infra_failure_streak += 1
    else:
        session.infra_failure_streak = 0
        if sig and sig == session.last_issues_signature:
            session.no_progress_count += 1
        else:
            session.no_progress_count = 0
            if sig:
                session.last_issues_signature = sig
    session.score_window.append(session.last_score)
    if len(session.score_window) > STAGNATION_WINDOW:
        session.score_window.pop(0)
async def _check_gates(session, round_no, bus):
    critical = any(
        issue.get("severity") == "CRITICAL"
        for issue in (session.history[-1].get("review", {}).get("issues") or [])
    ) if session.history else False
    if session.last_approve and session.last_score >= APPROVE_SCORE_THRESHOLD and not critical:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.completed",
            content=f"Loop approved after {round_no} round(s), score={session.last_score}",
            msg_type="result",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id, "score": session.last_score, "rounds": round_no},
        ))
        return "approved"
    if session.total_tokens_used >= COST_TOKEN_CAP:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.stopped",
            content=f"Loop stopped after {session.total_tokens_used} tokens (cap {COST_TOKEN_CAP}).",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id, "reason": "cost_token_cap", "rounds": round_no},
        ))
        return "cost_cap"
    if session.infra_failure_streak >= INFRA_FAILURE_LIMIT:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.stopped",
            content=f"Loop stopped after {session.infra_failure_streak} consecutive reviewer infrastructure failures.",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id, "reason": "infra_failure_streak", "rounds": round_no},
        ))
        return "infra_streak"
    if (len(session.score_window) >= STAGNATION_WINDOW
        and max(session.score_window) - min(session.score_window) <= STAGNATION_TOLERANCE
        and max(session.score_window) < APPROVE_SCORE_THRESHOLD):
        await bus.publish(Message(
            sender="orchestrator", topic="loop.stopped",
            content=f"Loop stopped: score flat at {session.score_window} for {STAGNATION_WINDOW} rounds.",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id, "reason": "score_stagnation", "rounds": round_no,
                      "score_window": list(session.score_window)},
        ))
        return "stagnation"
    if session.no_progress_count >= NO_PROGRESS_LIMIT:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.stopped",
            content=f"Loop stopped after {session.no_progress_count} rounds of no progress (same issues repeating).",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id, "reason": "no_progress", "rounds": round_no},
        ))
        return "no_progress"
    return "continue"
async def run_loop(session, requirement):
    bus = session.message_bus
    history_digest = load_history_digest(session.persistence, session.project.id)
    if history_digest:
        requirement = history_digest + "\n\nCurrent requirement:\n" + requirement
        await bus.publish(Message(
            sender="orchestrator", topic="loop.history_loaded",
            content=f"Loaded {len(history_digest)} chars of cross-loop history",
            msg_type="text",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id,
                      "history_chars": len(history_digest)},
        ))
    await bus.publish(Message(
        sender="orchestrator", topic="loop.started",
        content=f"Loop started for project {session.project.id}",
        msg_type="result",
        metadata={"project_id": session.project.id, "session_id": session.session_id},
    ))
    try:
        while not session.user_stopped and session.round < LOOP_SAFETY_CAP:
            session.round += 1
            round_no = session.round
            try:
                from kairos.tools.cache import wipe_round_cache
                wipe_round_cache(session.project.id, round_no)
            except Exception:
                pass
            decision = await _wait_for_plan_decision(session, round_no, bus)
            if decision in ("timeout", "reject"):
                return
            if not session.original_requirement:
                session.original_requirement = requirement
            coder_result = await _run_coder_round(session, requirement, round_no)
            if session.user_stopped:
                break
            workspace = Path(getattr(session.project, "work_dir", None) or getattr(session.project, "workspace", ""))
            precheck_hint, precheck_fixable = await _run_precheck(session, workspace, round_no, bus)
            review = await run_reviewer_round(session, coder_result, round_no, precheck_hint=precheck_hint)
            if session.user_stopped:
                break
            if precheck_hint:
                review["_precheck_hint"] = precheck_hint
            if precheck_fixable:
                review["_precheck_fixable"] = precheck_fixable
            session.round_tokens = (
                (len(coder_result or "") + len(review.get("summary", ""))) // 4
                + len(requirement) // 4
            )
            session.total_tokens_used += session.round_tokens
            _update_progress(session, review)
            history_entry = {
                "round": round_no,
                "coder_summary": (coder_result or "")[:500],
                "review": review,
                "no_progress_count": session.no_progress_count,
                "ts": time.time(),
            }
            session.history.append(history_entry)
            if session.persistence is not None:
                try:
                    session.persistence.save_loop_round(
                        session.project.id, session.session_id, round_no,
                        coder_summary=history_entry["coder_summary"], review=review,
                    )
                except Exception:
                    logger.debug("persist loop round failed", exc_info=True)
            cp_sha = _auto_checkpoint(session, round_no, session.last_score, session.last_approve, review.get("summary", ""))
            if session.persistence is not None:
                try:
                    from kairos.review.comments import verdict_to_comments
                    comments = verdict_to_comments(review, project_id=session.project.id, round_no=round_no)
                    session.persistence.save_review_comments(session.project.id, round_no, comments)
                except Exception:
                    logger.debug("save_review_comments failed", exc_info=True)
                if cp_sha:
                    try:
                        session.persistence.save_checkpoint(
                            session.project.id, session.session_id, round_no,
                            cp_sha, session.last_score, session.last_approve, review.get("summary", ""),
                        )
                    except Exception:
                        logger.debug("save_checkpoint failed", exc_info=True)
            try:
                from kairos.hooks import get_runner
                get_runner().loop_round(round_no, history_entry["coder_summary"], review, session.project.id)
            except Exception:
                logger.debug("loop_round hook failed", exc_info=True)
            await bus.publish(Message(
                sender="orchestrator", topic="loop.round_completed",
                content=json.dumps({"round": round_no, "approve": session.last_approve,
                                     "score": session.last_score, "summary": review.get("summary", ""),
                                     "no_progress_count": session.no_progress_count}, ensure_ascii=False),
                msg_type="result",
                metadata={"project_id": session.project.id, "session_id": session.session_id,
                          "round": round_no, "approve": session.last_approve, "score": session.last_score},
            ))
            gate = await _check_gates(session, round_no, bus)
            if gate != "continue":
                return
            requirement = build_next_prompt(session, review)
        await bus.publish(Message(
            sender="orchestrator", topic="loop.stopped",
            content=("Loop stopped by user." if session.user_stopped
                     else f"Loop stopped after reaching safety cap ({LOOP_SAFETY_CAP} rounds)."),
            msg_type="warning",
            metadata={"project_id": session.project.id, "session_id": session.session_id,
                      "reason": "user" if session.user_stopped else "cap", "rounds": session.round},
        ))
    except Exception as e:
        logger.exception("Loop crashed for %s", session.project.id)
        await bus.publish(Message(
            sender="orchestrator", topic="loop.error",
            content=str(e), msg_type="error",
            metadata={"project_id": session.project.id, "session_id": session.session_id},
        ))
