"""Main Coder <-> Reviewer loop orchestrator.

Hosts run_loop + the inner Coder round + auto-checkpoint helper.
The heavy lifting (gates, cross-loop memory, prompts, reviewers)
lives in dedicated submodules; this module is just the orchestrator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


async def _run_loop_reflection(session, gate: str, round_no: int, bus) -> None:
    """Best-effort Coder self-reflection at the end of a loop.

    Looks for a Coder agent on the session (via ``session.agents`` or
    the orchestrator's registry) and feeds it the round history. The
    reflection is saved to the project's memory and emitted on the
    bus as ``reflection.recorded``. Any failure is logged and
    swallowed; the loop outcome is unaffected.
    """
    try:
        from kairos.reflection import run_reflection
    except ImportError:
        return

    coder = None
    # Heuristics to find the Coder agent on the session. Different
    # session implementations use different attribute names; we try
    # them all.
    for attr in ("coder", "agents", "_agents"):
        obj = getattr(session, attr, None)
        if obj is None:
            continue
        if hasattr(obj, "generate") or hasattr(obj, "agenerate"):
            coder = obj
            break
        if isinstance(obj, dict):
            for k, v in obj.items():
                if hasattr(v, "generate") or hasattr(v, "agenerate"):
                    coder = v
                    break
            if coder:
                break
    if coder is None:
        return

    history = list(getattr(session, "history", []) or [])
    digests: list = []
    for entry in history:
        rev = entry.get("review") if isinstance(entry, dict) else None
        if not isinstance(rev, dict):
            continue
        digests.append({
            "round": entry.get("round", 0),
            "score": rev.get("score", 0),
            "approve": rev.get("approve", False),
            "notes": (rev.get("summary") or "")[:200],
        })

    requirement = getattr(getattr(session, "project", None), "requirement", "") or ""
    last_review = history[-1].get("review") if history else {}
    last_score = float(last_review.get("score", 0)) if isinstance(last_review, dict) else 0.0

    refl = await run_reflection(
        project_id=session.project.id,
        coder_agent=coder,
        requirement=str(requirement),
        outcome=gate,
        rounds=round_no,
        round_digests=digests,
        final_score=last_score,
    )

    try:
        await bus.publish(Message(
            sender="orchestrator",
            topic="reflection.recorded",
            content=refl.to_json(),
            msg_type="result",
            metadata={
                "project_id": session.project.id,
                "session_id": session.session_id,
                "outcome": gate,
                "rounds": round_no,
            },
        ))
    except Exception:
        logger.debug("reflection: failed to publish (non-fatal)", exc_info=True)


async def _fire_session_lifecycle_hooks(
    session, when: str, requirement: str = "", outcome: str = "",
) -> None:
    """Run ``SessionStart`` (when="start") or ``SessionEnd``
    (when="end") hooks against the default registry. No-op when
    the hook system isn't installed or the registry is empty.

    Errors are logged and swallowed so a misbehaving hook can
    never break the loop.
    """
    try:
        from kairos.hooks import (
            HookContext, HookEvent, get_default_registry,
        )
    except ImportError:
        return
    if when == "start":
        event = HookEvent.SESSION_START
    elif when == "end":
        event = HookEvent.SESSION_END
    else:
        return
    reg = get_default_registry()
    if not reg.hooks_for(event):
        return
    ctx = HookContext(
        event=event,
        project_id=getattr(session.project, "id", ""),
        metadata={
            "session_id": getattr(session, "session_id", ""),
            "requirement": requirement[:1000],
            "outcome": outcome,
            "round": getattr(session, "round", 0),
        },
    )
    try:
        await reg.run(ctx)
    except Exception:
        logger.debug("session %s hook failed (non-fatal)", when, exc_info=True)

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
         Coder is innocent; we just could not read its output.
      4. no_progress — exact same issue signature repeating; the Coder
         is stuck on the same failure mode. Fires at NO_PROGRESS_LIMIT.
      5. stagnation — score is flat near (but below) the approve
         threshold AND no_progress is small (we are plateauing, not
         looping on the same error). Requires score >= 60 so that
         genuinely stuck low-score rounds are caught by no_progress.
      6. safety_cap — hard round ceiling; the absolute backstop.

    Every return path also bumps the ``kairos_loop_rounds_total``
    Prometheus counter so dashboards can see how loops end over time.
    """
    from kairos.metrics import record_loop_round

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
        record_loop_round("approved")
        return "approved"
    if session.total_tokens_used >= COST_TOKEN_CAP:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.cost_cap",
            content=f"Token budget exhausted ({session.total_tokens_used} >= {COST_TOKEN_CAP})",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        record_loop_round("cost_cap")
        return "cost_cap"
    if session.infra_failure_streak >= INFRA_FAILURE_LIMIT:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.infra_streak",
            content=f"Infra failure streak {session.infra_failure_streak} >= {INFRA_FAILURE_LIMIT}",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        record_loop_round("infra_streak")
        return "infra_streak"
    if session.no_progress_count >= NO_PROGRESS_LIMIT:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.no_progress",
            content=f"No-progress counter {session.no_progress_count} >= {NO_PROGRESS_LIMIT}",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        record_loop_round("no_progress")
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
        record_loop_round("stagnation")
        return "stagnation"
    if session.round >= LOOP_SAFETY_CAP:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.safety_cap",
            content=f"Round {session.round} reached safety cap {LOOP_SAFETY_CAP}",
            msg_type="warning",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id},
        ))
        record_loop_round("safety_cap")
        return "safety_cap"
    return None

# ============================================================================
# Best-of-N + smart plan mode + round summary
# ============================================================================

async def _best_of_n_attempts(session, requirement, round_no, n, bus):
    """Run the Coder N times in parallel, score each by a quick self-grade
    on the Coder tail, and return the winning (coder_result, score) pair.

    Cheap self-grade: we ask the Coder to emit a brief self-eval in its
    tail ("CONFIDENCE: 0-100"). We parse that. No Reviewer calls — the
    real Reviewer runs after best-of-N picks a winner.

    Falls back to single-attempt mode if n<=1 or the Coder tail is
    unparseable (in which case we just use the first result).
    """
    if n <= 1 or not hasattr(session, "coder"):
        coder_result = await _run_coder_round(session, requirement, round_no)
        return coder_result, 0
    try:
        tasks = [
            asyncio.create_task(_run_coder_round(session, requirement, round_no))
            for _ in range(n)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        logger.debug("best-of-N spawn failed, using single attempt", exc_info=True)
        coder_result = await _run_coder_round(session, requirement, round_no)
        return coder_result, 0
    candidates: List[Tuple[int, int, str]] = []
    for idx, r in enumerate(results):
        if isinstance(r, Exception):
            logger.debug("best-of-N attempt %d raised: %s", idx, r)
            continue
        text = r if isinstance(r, str) else str(r or "")
        score = _parse_self_confidence(text)
        candidates.append((score, idx, text))
    if not candidates:
        first = results[0] if results else ""
        text = first if isinstance(first, str) else str(first or "")
        return text, 0
    candidates.sort(key=lambda item: (-item[0], item[1]))
    best_score, _, best_text = candidates[0]
    try:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.best_of_n_pick",
            content=f"Picked attempt {candidates[0][1]+1}/{n} (self-confidence={best_score})",
            msg_type="text",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id,
                      "round": round_no,
                      "best_of_n": n,
                      "candidates": [
                          {"idx": idx, "self_score": s} for s, idx, _ in candidates
                      ]},
        ))
    except Exception:
        pass
    return best_text, best_score

def _parse_self_confidence(coder_text: str) -> int:
    """Pull a 0-100 confidence score from the Coder tail if present.

    Format the Coder prompt teaches:
        CONFIDENCE: 78
        RISK: low
    Anything missing or unparseable -> 0 (neutral). The Coder is not graded
    on this; it is a soft signal so best-of-N can break ties.
    """
    if not coder_text:
        return 0
    tail = coder_text[-1200:]
    m = re.search(r"CONFIDENCE\s*[:=]\s*(\d{1,3})", tail, re.IGNORECASE)
    if not m:
        return 0
    try:
        val = int(m.group(1))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, val))

def _is_trivial_requirement(requirement: str) -> bool:
    """Heuristic: a requirement is "trivial" when it is small, single-file,
    and has no architectural keywords. Plan mode adds latency for trivial
    tasks; we auto-approve those.
    """
    if not requirement:
        return True
    text = requirement.strip()
    if len(text) <= 200 and "\n\n" not in text:
        return True
    lowered = text.lower()
    heavy_signals = (
        "architecture", "refactor", "migrate", "rewrite",
        "redesign", "multi-file",
    )
    if any(sig in lowered for sig in heavy_signals):
        return False
    file_path_count = sum(
        1 for line in text.splitlines() if "`" in line and "." in line
    )
    if file_path_count >= 3:
        return False
    return len(text) <= 600

def should_auto_approve_plan(requirement: str) -> bool:
    """Public predicate so the orchestrator can call this before launch."""
    return _is_trivial_requirement(requirement)

def _build_round_summary(session, coder_result: str, review: dict, round_no: int) -> str:
    """One-paragraph round digest for the UI / loop_history table.

    Captures: outcome, score, top issues, files-touched signal. Designed
    to be cheap to display and easy to grep in cross-loop memory.
    """
    parts = []
    if review.get("approve"):
        parts.append(f"APPROVED (score={review.get('score', 0)})")
    else:
        parts.append(f"rejected (score={review.get('score', 0)})")
    issues = review.get("issues") or []
    if issues:
        top = issues[:3]
        joined = "; ".join(
            f"{i.get('severity','?')}:{i.get('file','?')}:{i.get('description','')[:60]}"
            for i in top
        )
        parts.append(f"top issues: {joined}")
    if session.history and len(session.history) >= 2:
        prev = session.history[-2]["review"] if len(session.history) >= 2 else {}
        prev_score = prev.get("score", 0) or 0
        delta = (review.get("score", 0) or 0) - prev_score
        if delta:
            parts.append(f"score delta={delta:+d}")
    summary = (review.get("summary") or "").strip()
    if summary:
        parts.append(f"summary: {summary[:200]}")
    return " | ".join(parts)

async def _maybe_auto_approve_plan(session, round_no, bus, requirement: str):
    """If the user has not engaged plan mode AND the requirement is
    trivial, mark the plan as auto-approved so the loop skips the
    user-blocking plan wait. Emits a message so the UI can show
    "auto-approved" instead of pending.
    """
    if session.plan_completed or session.plan_decision:
        return False
    if not _is_trivial_requirement(requirement):
        return False
    session.plan_decision = "approve"
    session.plan_pending = False
    session.plan_completed = True
    try:
        await bus.publish(Message(
            sender="orchestrator", topic="loop.plan_auto_approved",
            content="Plan auto-approved (trivial requirement detected).",
            msg_type="result",
            metadata={"project_id": session.project.id,
                      "session_id": session.session_id,
                      "round": round_no},
        ))
    except Exception:
        pass
    return True

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

    # Round 5: fire SessionStart hooks. Best-effort — failures
    # never block the loop from running.
    try:
        await _fire_session_lifecycle_hooks(
            session, "start", requirement=requirement,
        )
    except Exception:
        logger.debug("SessionStart hooks failed (non-fatal)", exc_info=True)

    try:
        while not session.user_stopped and session.round < LOOP_SAFETY_CAP:
            session.round += 1
            round_no = session.round
            try:
                from kairos.tools.cache import clear_round
                clear_round()
            except Exception:
                pass
            # Auto-approve trivial plans so trivial tasks do not block on the user.
            try:
                await _maybe_auto_approve_plan(session, round_no, bus, requirement)
            except Exception:
                logger.debug("plan auto-approve failed (non-fatal)", exc_info=True)
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
            # Best-of-N: spawn multiple Coders in parallel, pick highest-confidence winner.
            best_of_n = max(1, min(int(getattr(session, "best_of_n", 1) or 1), 5))
            if best_of_n > 1:
                coder_result, _self_score = await _best_of_n_attempts(
                    session, requirement, round_no, best_of_n, bus,
                )
            else:
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
            try:
                review["_round_summary"] = _build_round_summary(
                    session, coder_result, review, round_no
                )
            except Exception:
                logger.debug("round summary build failed (non-fatal)", exc_info=True)
            _update_progress(session, review)
            session.history.append({"round": round_no, "review": review, "coder": coder_result[:2000]})

            # ---- Memory writes: persist this round's digest + record
            # working-fix patterns so the next round can short-circuit
            # similar failures. All best-effort; never break the loop.
            try:
                pid = session.project.id
                sid = session.session_id
                persistence = getattr(session, "persistence", None)
                if persistence is not None:
                    # Save review comments so the next Coder prompt sees them.
                    try:
                        from kairos.review.comments import verdict_to_comments
                        comments = verdict_to_comments(review, project_id=pid, round_no=round_no)
                        if comments:
                            persistence.save_review_comments(pid, round_no, comments)
                    except Exception:
                        logger.debug("memory: save_review_comments failed", exc_info=True)
                    # Mirror the round into the FTS index so the next
                    # memory block can find it by keyword.
                    try:
                        issues_text = "; ".join(
                            (i.get("description") or "")
                            for i in (review.get("issues") or [])
                        )[:1500]
                        persistence.index_loop_round(
                            pid, sid, round_no,
                            coder_summary=(coder_result or "")[:400],
                            review_summary=(review.get("summary") or "")[:400],
                            issues_text=issues_text,
                        )
                    except Exception:
                        logger.debug("memory: index_loop_round failed", exc_info=True)
                    # Working-fix: only on a fail -> pass transition.
                    try:
                        from kairos.memory.growth import maybe_record_working_fix
                        prior_review = (session.history[-2]["review"]
                                        if len(session.history) >= 2 else None)
                        maybe_record_working_fix(
                            persistence, pid, prior_review, review, coder_result,
                        )
                    except Exception:
                        logger.debug("memory: maybe_record_working_fix failed", exc_info=True)
                    # Promote repeated-issue categories to a never-rule.
                    try:
                        from kairos.memory.growth import auto_promote_failure_to_preference
                        try:
                            review_conf = float(review.get("_confidence") or 0.5)
                        except (TypeError, ValueError):
                            review_conf = 0.5
                        for issue in (review.get("issues") or [])[:5]:
                            auto_promote_failure_to_preference(
                                persistence, pid, issue,
                                consecutive_count=3, threshold=3,
                                confidence=review_conf,
                            )
                    except Exception:
                        logger.debug("memory: auto_promote failed", exc_info=True)
                    # Promote CRITICAL/MAJOR issues into the global KB.
                    try:
                        from kairos.memory.growth import record_global_insights_from_review
                        record_global_insights_from_review(persistence, pid, review)
                    except Exception:
                        logger.debug("memory: global insights failed", exc_info=True)
            except Exception:
                logger.debug("memory: round-end writes failed", exc_info=True)

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
                # Best-effort Coder self-reflection. The loop is already
                # over; failure here must not crash the orchestrator.
                try:
                    await _run_loop_reflection(session, gate, round_no, bus)
                except Exception:
                    logger.debug("post-loop reflection failed (non-fatal)", exc_info=True)
                # Round 5: fire SessionEnd hooks (mirrors SessionStart).
                try:
                    await _fire_session_lifecycle_hooks(
                        session, "end", outcome=gate,
                    )
                except Exception:
                    logger.debug("SessionEnd hooks failed (non-fatal)", exc_info=True)
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