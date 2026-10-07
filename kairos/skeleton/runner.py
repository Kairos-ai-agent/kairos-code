"""Background runner for routed skeleton tasks -- the non-code path of ``/start``.

Why this exists
---------------
The Coder <-> Reviewer loop already runs in the background: ``Orchestrator.start_loop``
does its cheap setup and then ``asyncio.create_task(run_loop(...))``, so the HTTP
handler returns immediately and the UI watches progress over the bus
(``kairos/core/orchestrator.py:998``). The first cut of skeleton routing did the
opposite -- it ``await``-ed the whole worker+verifier *inside the request*
(``api/routes/projects.py:230`` before this change), so with a real model the
request would sit there for the whole run. This module gives the skeleton route
the loop's shape:

* the request **schedules an asyncio task and returns at once** with a run id
  and ``status="running"`` -- it never waits for the worker/verifier;
* the run's live state hangs off the project (``project.skeleton_state``), so a
  read-only endpoint (``GET /api/projects/{id}/skeleton``) can report it, exactly
  as ``GET /loop`` reports ``project.loop_session`` / ``project.loop_task``;
* stopping (``POST .../skeleton/stop``) cancels the task, and a done-callback
  publishes a terminal ``skeleton.ended`` event on the message bus -- the same
  "the UI must never be stuck on 运行中" fix the loop's ``loop.ended`` is
  (``kairos/core/orchestrator_parts/loopctl.py:126``). Every terminal path
  (done / failed / stopped) publishes it exactly once, from *after* the task has
  really finished, so a subsequent state read agrees with the event.

The worker+verifier themselves are the unchanged :func:`run_general_task`; this
module only wraps it in a task, tracks state, and announces the end. It touches
no loop code.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

#: Directory (relative to the workspace root) the run record is persisted under,
#: mirroring ``run_general_task``'s default.
RUN_DIR_REL = Path(".kairos") / "skeleton-runs"

#: The terminal topic the runner publishes, mirroring ``loop.ended``.
ENDED_TOPIC = "skeleton.ended"

#: Keep a strong reference to fire-and-forget publish tasks so the event loop
#: cannot garbage-collect one mid-flight (the same reason the loop keeps
#: ``_dispatch_tasks``).
_PENDING_PUBLISHES: Set[asyncio.Task] = set()


@dataclass
class SkeletonState:
    """Live state of one background skeleton run.

    ``status`` is the runner's own lifecycle word and never lies about a
    finished task: ``running`` -> ``done`` | ``failed`` | ``stopped``.
    ``outcome`` is the run's own verdict word (``passed`` / ``failed`` /
    ``needs_human`` / ``undecided``) once the verifier has spoken.
    """

    run_id: str
    project_id: str
    workspace_kind: str
    requirement: str
    origin: str = ""                       # "explicit" / "heuristic" / "default"
    reason: str = ""
    status: str = "running"
    outcome: str = ""
    passed: Optional[bool] = None
    verdict: Optional[Dict[str, Any]] = None
    run_file: str = ""
    artifacts: List[str] = field(default_factory=list)
    error: str = ""
    stop_requested: bool = False
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0
    task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)
    bus: Any = field(default=None, repr=False, compare=False)

    @property
    def session_id(self) -> str:
        """Alias for ``run_id`` so callers that speak "session id" (the loop's
        response name) can read the same handle."""
        return self.run_id

    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "workspace_kind": self.workspace_kind,
            "route": "skeleton",
            "route_source": self.origin,
            "route_reason": self.reason,
            "status": self.status,
            "running": self.running(),
            "outcome": self.outcome,
            "passed": self.passed,
            "verdict": self.verdict,
            "run_file": self.run_file,
            "artifacts": list(self.artifacts),
            "error": self.error,
            "stop_requested": self.stop_requested,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "message": self.requirement,
        }


def _schedule(listener) -> None:
    """Run ``listener`` as a task on the current loop, holding a reference.

    Best-effort: called from a ``Task.add_done_callback`` (a sync frame) and
    from the request coroutine. A failure to schedule must never propagate.
    """
    try:
        task = asyncio.create_task(listener)
    except Exception:
        logger.debug("skeleton event scheduling failed", exc_info=True)
        return
    _PENDING_PUBLISHES.add(task)
    task.add_done_callback(_PENDING_PUBLISHES.discard)


async def _execute(
    state: SkeletonState,
    *,
    root: str,
    instruction: str,
    kind: str,
    generate,
    bus,
    run_dir: Path,
) -> None:
    """The background body: run the (unchanged) skeleton, fill ``state``."""
    from kairos.skeleton.service import run_general_task

    outcome = await run_general_task(
        kind=kind,
        root=root,
        instruction=instruction,
        generate=generate,
        run_id=state.run_id,
        bus=bus,
        run_dir=run_dir,
    )
    run = outcome.run
    state.run_id = run.run_id
    state.outcome = run.outcome
    state.passed = run.passed
    state.verdict = run.verdict.to_dict()
    state.artifacts = list(outcome.artifacts or [])
    if outcome.run_file:
        state.run_file = outcome.run_file
    logger.info(
        "skeleton run: project=%s kind=%s run=%s outcome=%s",
        state.project_id, kind, run.run_id, run.outcome,
    )


def _announce_ended(state: SkeletonState) -> None:
    """Publish the terminal ``skeleton.ended`` event (mirrors ``loop.ended``)."""
    if state.bus is None:
        return
    try:
        from kairos.core.message_bus import Message

        msg = Message(
            sender="skeleton",
            topic=ENDED_TOPIC,
            content=f"Skeleton run ended ({state.status})",
            msg_type="result",
            metadata={
                "project_id": state.project_id,
                "session_id": state.session_id,
                "run_id": state.run_id,
                "workspace_kind": state.workspace_kind,
                "status": state.status,
                "outcome": state.outcome,
            },
        )
        _schedule(state.bus.publish(msg))
    except Exception:  # announcing must never affect the bookkeeping below
        logger.debug("skeleton.ended publish failed for %s",
                     state.project_id, exc_info=True)


def finalize_skeleton_run(state: SkeletonState, task: asyncio.Task) -> None:
    """Done-callback: set the terminal status and announce it.

    Runs on the event loop *after* the task has really finished, so
    ``task.done()`` is true by now and a state read taken on the back of the
    event agrees with it. Cancelled -> ``stopped`` (the Stop button), an
    exception -> ``failed``, otherwise ``done``.
    """
    if task.cancelled():
        state.status = "stopped"
    else:
        exc = None
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            state.status = "stopped"
        if exc is not None:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"[:400]
            logger.exception("skeleton run for %s raised", state.project_id,
                             exc_info=exc)
        elif state.status != "stopped":
            state.status = "done"
    state.ended_at = time.time()
    _announce_ended(state)


def start_skeleton_run(
    *,
    project,
    kind: str,
    root: str,
    instruction: str,
    generate,
    bus=None,
    run_id: str = "",
    origin: str = "",
    reason: str = "",
) -> SkeletonState:
    """Schedule one skeleton run in the background; return its state at once.

    The caller (the HTTP route) returns the moment this returns -- nothing here
    awaits the worker. The state is attached to ``project`` as
    ``project.skeleton_state`` so a later read/stop can find it, and the task
    carries a done-callback that stamps the terminal status and announces it.
    """
    run_dir = Path(str(root)).expanduser() / RUN_DIR_REL
    state = SkeletonState(
        run_id=run_id or uuid.uuid4().hex[:8],
        project_id=str(getattr(project, "id", "") or ""),
        workspace_kind=kind,
        requirement=instruction,
        origin=origin,
        reason=reason,
        bus=bus,
    )
    state.run_file = str(run_dir / f"skeleton-run-{state.run_id}.json")
    project.skeleton_state = state
    state.task = asyncio.create_task(
        _execute(state, root=str(root), instruction=instruction, kind=kind,
                 generate=generate, bus=bus, run_dir=run_dir),
        name=f"skeleton-{state.project_id}",
    )
    state.task.add_done_callback(lambda t: finalize_skeleton_run(state, t))
    return state


def stop_skeleton_run(project) -> bool:
    """Cancel a running skeleton task; mirror the loop's stop semantics.

    Returns ``True`` when a live task was cancelled (the done-callback then
    stamps ``stopped`` and publishes ``skeleton.ended``), ``False`` when there
    was nothing running.
    """
    state = getattr(project, "skeleton_state", None)
    if state is None or state.task is None or state.task.done():
        return False
    state.stop_requested = True
    state.task.cancel()
    return True


def read_skeleton_state(project) -> Optional[Dict[str, Any]]:
    """The project's skeleton state as a plain dict, or ``None`` if it never ran."""
    state = getattr(project, "skeleton_state", None)
    if state is None:
        return None
    return state.to_dict()


__all__ = [
    "SkeletonState",
    "start_skeleton_run",
    "stop_skeleton_run",
    "read_skeleton_state",
    "finalize_skeleton_run",
    "ENDED_TOPIC",
    "RUN_DIR_REL",
]
