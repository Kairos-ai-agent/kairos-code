"""Durable tasks — a run that survives the app being closed.

`kairos/har.py` has been the state layer for that contract since Round 29:
`.har/contract.json` (the goal, immutable), `state.json` (round, score, plan),
`plan.md`, a JSONL history, and a PID lock, with a `resume()` that runs the next
round and saves. Nothing in the app ever called it — the only entry point was
the `kairos.har` CLI — so "kick off a multi-day refactor and pick it up
tomorrow" required a terminal. This module is the app-side service: it binds a
project's durable task to that project's real Coder/Reviewer loop and exposes
start / resume / status over HTTP.

**What a tick is.** One tick is one run of the project's loop (the same
`run_loop` a normal Start uses: plan mode, reviewers, precheck, gates). The
durable layer's contribution is that the outcome is written to `.har/` before
the next tick begins, so an app restart resumes from the last saved tick instead
of from the beginning. An interrupted tick loses the round in flight, not the
task — and the round in flight is already checkpointed by the loop itself.

**One task per project.** `har` hardcodes a single `.har/` directory per root,
and a durable task *is* that contract, so the second `start` on a project is
refused rather than silently overwriting the first one's history.

**Why the async variant.** `har.resume()` takes a synchronous `tick_fn`, and
our tick drives the project's loop, whose LLM clients are bound to the app's
event loop. Running that on a worker thread with its own loop would break them,
so `har.resume_async` is the same state machine with an awaited tick.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from kairos import har

logger = logging.getLogger(__name__)

# How long one tick may run before we give up on it and let the state layer
# record what it has. A loop run is bounded by its own caps; this is the outer
# stop for a wedged provider.
TICK_TIMEOUT_S = 3600.0


def task_root(project: Any) -> Path:
    """The directory the `.har/` contract lives in: the project's work dir.

    Deliberately the work dir rather than the app's data dir: `plan.md` and the
    history belong next to the code they describe, and `har` writes its own
    `.gitignore` for the lock file.
    """
    root = getattr(project, "work_dir", "") or getattr(project, "workspace", None)
    if root is None:
        raise ValueError("project has no work_dir or workspace")
    return Path(root)


def har_dir(project: Any) -> Path:
    return task_root(project) / har.DEFAULT_HAR_DIRNAME


def is_durable(project: Any) -> bool:
    return (har_dir(project) / "contract.json").exists()


def is_locked(project: Any) -> bool:
    """Whether a resume currently holds the lock (cosmetic: best effort)."""
    lock = har_dir(project) / "lock"
    if not lock.exists():
        return False
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    return age < har.STALE_LOCK_S


def status(project: Any) -> Optional[Dict[str, Any]]:
    """Everything the UI needs about this project's durable task, or None."""
    if project is None or not is_durable(project):
        return None
    try:
        contract, state, h = har.load_har(task_root(project))
    except Exception as e:  # noqa: BLE001
        logger.debug("durable status failed: %s", e)
        return None
    return {
        "har_id": contract.har_id,
        "goal": contract.goal,
        "created_at": contract.created_at,
        "cwd": contract.cwd,
        "round": state.round,
        "rounds_planned": contract.rounds_planned,
        "last_score": state.last_score,
        "last_approve": state.last_approve,
        "last_summary": state.last_summary,
        "no_progress_count": state.no_progress_count,
        "updated_at": state.updated_at,
        "plan_text": state.plan_text,
        "locked": is_locked(project),
        "root": str(h),
        "history": har.read_history(h, limit=10),
    }


def start(project: Any, goal: str, *, rounds_planned: int = 10) -> Dict[str, Any]:
    """Create the durable task for this project. One per project."""
    goal = (goal or "").strip()
    if not goal:
        return {"ok": False, "error": "goal is required"}
    root = task_root(project)
    if is_durable(project):
        return {"ok": False,
                "error": "a durable task already exists for this project; "
                         "resume it or remove its .har/ directory"}
    contract = har.HarContract(
        goal=goal,
        har_id=uuid.uuid4().hex[:8],
        created_at=time.time(),
        cwd=str(root),
        rounds_planned=max(1, int(rounds_planned or 10)),
    )
    try:
        h = har.init_har(root, contract)
    except Exception as e:  # noqa: BLE001
        logger.warning("durable start failed for %s: %s", root, e)
        return {"ok": False, "error": str(e)}
    logger.info("durable task %s created in %s", contract.har_id, h)
    return {"ok": True, "har_id": contract.har_id, **status(project)}


def _verdict_of(session: Any) -> Dict[str, Any]:
    """The last round's outcome, read off the loop session."""
    history = getattr(session, "history", None) or []
    entry = history[-1] if history else {}
    if not isinstance(entry, dict):
        return {}
    return entry


def _approved(entry: Dict[str, Any]) -> bool:
    review = entry.get("review") or {}
    if not isinstance(review, dict):
        return False
    verdict = str(review.get("verdict") or review.get("decision") or "").lower()
    return bool(review.get("approved")) or verdict in ("approve", "approved", "yes")


def _score(entry: Dict[str, Any]) -> int:
    review = entry.get("review") or {}
    for candidate in (entry.get("score"),
                      review.get("score") if isinstance(review, dict) else None,
                      entry.get("objective_signal")):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return 0


def _record_artifacts(project_id: str, session: Any, round_no: int,
                      summary: str, approved: bool, score: int,
                      plan: str) -> None:
    """Keep what the round produced where a person can read and answer it.

    The transcript is not an archive: a plan scrolled past in a chat is gone in
    the sense that matters, because nobody can link to it or reply to it.
    Best effort by design -- see kairos/artifacts.py.
    """
    try:
        from kairos import artifacts
    except Exception:  # noqa: BLE001
        return
    session_id = str(getattr(session, "session_id", "") or "")
    if plan:
        artifacts.record_plan(project_id, plan, session_id=session_id,
                              round_no=round_no)
    if summary:
        artifacts.record_summary(project_id, summary, session_id=session_id,
                                 round_no=round_no, approved=approved,
                                 score=score)


async def resume(orchestrator: Any, project_id: str, *, ticks: int = 1,
                 timeout_s: float = TICK_TIMEOUT_S) -> Dict[str, Any]:
    """Run the project's loop up to `ticks` times, saving state after each.

    Returns the state layer's ``(returncode, message)`` plus the fresh status.
    ``rc`` 4 means the loop approved the work and the durable task is done; 0
    means ticks ran without approval; 2 means another resume holds the lock.
    """
    project = orchestrator.get_project(project_id)
    if project is None:
        return {"ok": False, "code": 3,
                "error": "unknown project: " + str(project_id)}
    if not is_durable(project):
        return {"ok": False, "code": 3,
                "error": "no durable task for this project yet"}
    root = task_root(project)
    goal = har.load_har(root)[0].goal

    async def tick(state: "har.HarState", contract: "har.HarContract"):
        try:
            await orchestrator.start_loop(project_id, contract.goal)
        except ValueError as e:
            # start_loop refuses when a loop is already running. That is a real
            # answer, not a crash, and it must not be recorded as progress: the
            # tick raises so the state layer advances nothing and releases the
            # lock, leaving the task exactly where it was.
            logger.warning("durable tick could not start the loop: %s", e)
            raise RuntimeError("loop already running for this project: " + str(e))
        task = getattr(project, "loop_task", None)
        if task is not None:
            try:
                # Shielded: on timeout the round keeps running and saves its own
                # checkpoints; the next tick picks up the loop's outcome.
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
            except asyncio.TimeoutError:
                logger.warning("durable tick timed out after %ss", timeout_s)
        session = getattr(project, "loop_session", None)
        entry = _verdict_of(session)
        approved = _approved(entry)
        score = _score(entry)
        summary = str(entry.get("summary") or entry.get("coder_summary") or "")
        plan_text = ""
        try:
            plan = getattr(session, "plan_text", "") or ""
            plan_text = str(plan)
        except Exception:  # noqa: BLE001
            plan_text = ""
        new_state = har.HarState(**{
            **state.to_dict(),
            "round": state.round + 1,
            "last_score": score,
            "last_approve": approved,
            "last_summary": summary[:600],
            "plan_text": plan_text or state.plan_text,
            "updated_at": time.time(),
        })
        history_entry = {
            "round": new_state.round,
            "ts": time.time(),
            "score": score,
            "approved": approved,
            "summary": summary[:600],
            "session_id": getattr(session, "session_id", ""),
        }
        _record_artifacts(project_id, session, new_state.round, summary,
                          approved, score, plan)
        return new_state, history_entry

    try:
        rc, message = await har.resume_async(
            root, max_rounds=max(1, int(ticks or 1)), tick_fn=tick,
            stop_on_approve=True,
        )
    except Exception as e:  # noqa: BLE001
        # A tick that could not run (a loop already in flight, a provider that
        # died) must leave the task where it was, not half-advanced.
        logger.warning("durable resume %s failed: %s", project_id, e)
        return {"ok": False, "code": -1, "message": str(e), "goal": goal,
                "status": status(project)}
    logger.info("durable resume %s: rc=%s %s", project_id, rc, message)
    return {
        "ok": rc in (0, 4),
        "code": rc,
        "message": message,
        "goal": goal,
        "status": status(project),
    }
