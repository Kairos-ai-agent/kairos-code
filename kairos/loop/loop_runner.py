"""Main Coder <-> Reviewer loop orchestrator.

Hosts run_loop + the inner Coder round + auto-checkpoint helper.
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
    COST_TIME_CAP_S,
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
    if session.plan_decision == "reject":
        await bus.publish(Message(
            sender="orchestrator", topic="loop.rejected",
            content="Plan rejected by user.",
            msg_type="result",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        return "reject"
    if session.plan_decision == "approve":
        session.plan_completed = True
        return "proceed"
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
    precheck_fixable = []
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


def _maybe_rollback_on_regression(session, coder_result: str) -> bool:
    """If the latest round's score regressed vs the previous, roll the
    workspace back to the previous checkpoint. Returns True when a
    rollback actually happened.

    Heuristic: drop >= 15 points from previous best triggers rollback,
    but only if we have a checkpoint to roll back to.
    """
    try:
        prev_best = int(getattr(session, "_best_score", 0) or 0)
        new_score = int(getattr(session, "last_score", 0) or 0)
        if not prev_best or new_score >= prev_best - 15:
            session._best_score = max(prev_best, new_score)
            return False
        from kairos.tools.checkpoint import revert_to_last
        workspace = Path(getattr(session.project, "work_dir", None)
                         or getattr(session.project, "workspace", ""))
        if not workspace:
            return False
        ok = revert_to_last(Path(workspace), session.round - 1)
        if ok:
            session._rollback_count = int(
                getattr(session, "_rollback_count", 0) or 0
            ) + 1
            session.history.append({
                "round": session.round,
                "rollback": True,
                "reason": f"score regressed {prev_best}->{new_score}",
            })
        session._best_score = max(prev_best, new_score)
        return bool(ok)
    except Exception:
        logger.debug("regression rollback failed (non-fatal)", exc_info=True)
        return False


def _update_progress(session, review):
    """Update session progress counters after a Reviewer round.

    Counter semantics:
      - no_progress_count increments when the issue signature repeats
        verbatim. A brand-new signature resets the counter to 1 so that
        after exactly NO_PROGRESS_LIMIT rounds of identical issues the
        counter equals NO_PROGRESS_LIMIT and the gate fires the same
        round — not one round later.
      - infra_failure_streak counts consecutive rounds where the Reviewer
        could not grade (timeout, parse fail, tool limit). Resets whenever
        a real verdict comes back, and also clears no_progress so a
        recovered Reviewer does not inherit a "stuck" counter from before.
    """
    session.last_score = review.get("score", 0)
    session.last_approve = review.get("approve", False)
    infra_failure = review.get("_failure_mode") in ("infra_fail", "tool_limit", "parse_fail")
    sig = issues_signature(review.get("issues") or [])
    if infra_failure:
        session.infra_failure_streak += 1
        # Signature is unreliable on infra failures; reset so a recovered
        # Reviewer does not inherit a "stuck" counter from before.
        session.no_progress_count = 0
        session.last_issues_signature = None
    else:
        session.infra_failure_streak = 0
        if sig:
            if sig == session.last_issues_signature:
                session.no_progress_count += 1
            else:
                session.no_progress_count = 1
                session.last_issues_signature = sig
        else:
            session.no_progress_count = 0
            session.last_issues_signature = None
    session.score_window.append(session.last_score)
    if len(session.score_window) > STAGNATION_WINDOW:
        session.score_window.pop(0)

async def _check_gates(session, round_no, bus):
    """Evaluate termination gates. Returns the gate name or None.

    Order matters:
      1. approved — round reached the score/approval threshold with no
         CRITICAL issue. Always checked first; once approved the loop is
         done.
      2. cost_cap — token budget exhausted. Cheaper to stop than to
         keep hitting the API.
      3. infra_streak — Reviewer kept failing to grade (timeout / parse
         fail / tool limit). Different from no_progress because the
         Coder is innocent; we just couldn't read its output.
      4. no_progress — exact same issue signature repeating; the Coder
         is stuck on the same failure mode. Fires at NO_PROGRESS_LIMIT.
      5. stagnation — score is flat near (but below) the approve
         threshold AND no_progress is small (we are plateauing, not
         looping on the same error). Requires score >= 60 so that
         genuinely stuck low-score rounds are caught by no_progress.
      6. safety_cap — hard round ceiling; the absolute backstop.
    """
    history = list(getattr(session, "history", []) or [])
    last_review = history[-1].get("review") if history else {}
    critical = any(
        issue.get("severity") == "CRITICAL"
        for issue in (last_review.get("issues") or [])
    ) if last_review else False
    if session.last_approve and session.last_score >= APPROVE_SCORE_THRESHOLD and not critical:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.completed",
            content=f"Loop approved after {round_no} round(s), score={session.last_score}",
            msg_type="result",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id,
                      "score": session.last_score, "rounds": round_no},
        ))
        return "approved"
    if session.total_tokens_used >= COST_TOKEN_CAP:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.cost_cap",
            content=f"Token budget exhausted ({session.total_tokens_used} >= {COST_TOKEN_CAP})",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        return "cost_cap"
    if session.infra_failure_streak >= INFRA_FAILURE_LIMIT:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.infra_streak",
            content=f"Infra failure streak {session.infra_failure_streak} >= {INFRA_FAILURE_LIMIT}",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        return "infra_streak"
    if session.no_progress_count >= NO_PROGRESS_LIMIT:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.no_progress",
            content=f"No-progress counter {session.no_progress_count} >= {NO_PROGRESS_LIMIT}",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        return "no_progress"
    score_window = list(getattr(session, "score_window", []) or [])
    if (len(score_window) >= STAGNATION_WINDOW
            and score_window
            and (max(score_window) - min(score_window)) <= STAGNATION_TOLERANCE
            and score_window[-1] >= APPROVE_SCORE_THRESHOLD - 15
            and score_window[-1] < APPROVE_SCORE_THRESHOLD):
        await bus.publish(Message(
            sender="orchestrator", topic="loop.stagnation",
            content=(
                f"Score plateau near {score_window[-1]} for {STAGNATION_WINDOW} rounds; needs a new approach"
            ),
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        return "stagnation"
    if session.round >= LOOP_SAFETY_CAP:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.safety_cap",
            content=f"Round {session.round} reached safety cap {LOOP_SAFETY_CAP}",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        return "safety_cap"
    return None

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
                from kairos.tools.cache import clear_round
                clear_round()
            except Exception:
                pass
            needs_plan = (
                not session.plan_completed
                and (
                    (getattr(session, "plan_pending", False)
                     and getattr(session, "plan_event", None) is not None)
                    or getattr(session, "plan_decision", None) == "reject"
                )
            )
            if needs_plan:
                plan_result = await _run_coder_round(session, requirement, round_no, plan_mode=True)
                if session.user_stopped:
                    break
            decision = await _wait_for_plan_decision(session, round_no, bus)
            if decision in ("timeout", "reject"):
                return
            if not session.original_requirement:
                session.original_requirement = requirement
            coder_result = await _run_coder_round(session, requirement, round_no)
            if session.user_stopped:
                break
            try:
                coder_text = coder_result if isinstance(coder_result, str) else str(coder_result or "")
                session.round_tokens = max(1, len(coder_text) // 4)
                session.total_tokens_used += session.round_tokens
            except Exception:
                pass
            workspace = Path(getattr(session.project, "work_dir", None) or getattr(session.project, "workspace", ""))
            precheck_hint, precheck_fixable = await _run_precheck(session, workspace, round_no, bus)
            if getattr(session, "specialist_reviewers", None):
                from kairos.loop import review_loop as _rl_mod
                review = await _rl_mod._run_reviewers_parallel(session, coder_result, round_no)
            else:
                from kairos.loop import review_loop as _rl_mod
                review = await _rl_mod._run_reviewer_round(session, coder_result, round_no, precheck_hint=precheck_hint)
            try:
                reviewer_text = json.dumps(review, ensure_ascii=False) if isinstance(review, dict) else str(review or "")
                session.round_tokens = max(1, len(reviewer_text) // 4)
                session.total_tokens_used += session.round_tokens
            except Exception:
                pass
            if session.user_stopped:
                break
            review["_precheck_hint"] = precheck_hint
            review["_precheck_fixable"] = precheck_fixable
            _update_progress(session, review)
            session.history.append({"round": round_no, "review": review, "coder": coder_result[:2000]})
            try:
                if _maybe_rollback_on_regression(session, coder_result):
                    await bus.publish(Message(
                        sender="orchestrator", topic="loop.regression_rollback",
                        content="Score regressed; workspace rolled back to last known-good checkpoint",
                        msg_type="warning",
                        metadata={"project_id": session.project.id,
                                  "session_id": session.session_id,
                                  "round": round_no},
                    ))
            except Exception:
                logger.debug("regression check failed (non-fatal)", exc_info=True)
            try:
                _auto_checkpoint(session, round_no, session.last_score, session.last_approve, review.get("summary", ""))
            except Exception:
                logger.debug("auto-checkpoint failed", exc_info=True)
            gate = await _check_gates(session, round_no, bus)
            if gate:
                return
        await bus.publish(Message(
            sender="orchestrator", topic="loop.finished",
            content=f"Loop finished after {session.round} round(s)",
            msg_type="result",
            metadata={"project_id": session.project.id, "session_id": session.session_id},
        ))
    except Exception as e:
        logger.exception("run_loop crashed")
        await bus.publish(Message(
            sender="orchestrator", topic="loop.error",
            content=f"Loop crashed: {e}",
            msg_type="error",
            metadata={"project_id": session.project.id, "session_id": session.session_id},
        ))

