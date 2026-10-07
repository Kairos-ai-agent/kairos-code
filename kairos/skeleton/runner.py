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
import json
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

#: The persisted run record's filename pattern inside :data:`RUN_DIR_REL`.
RUN_FILE_GLOB = "skeleton-run-*.json"

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
    #: The owning project, so the terminal done-callback can clear the project
    #: row's "运行中" marker. Best-effort; never serialised.
    project: Any = field(default=None, repr=False, compare=False)

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


def _set_project_status(project, status: str) -> None:
    """Reflect a run's lifecycle on ``project.status`` (best-effort).

    The project list renders "运行中" straight off ``project.status`` (the loop
    sets it at start / done). The skeleton must do the same or the two halves of
    one screen disagree: a finished run left the row on 运行中 while the panel
    said 未运行. Persisted so the marker survives a reload; never fatal.
    """
    if project is None:
        return
    try:
        project.status = status
        db = getattr(project, "_db", None)
        if db is not None:
            db.save_project(project)
    except Exception:  # a bookkeeping failure must not fail the run
        logger.debug("skeleton: could not set project status=%s", status,
                     exc_info=True)


def finalize_skeleton_run(state: SkeletonState, task: asyncio.Task) -> None:
    """Done-callback: set the terminal status and announce it.

    Runs on the event loop *after* the task has really finished, so
    ``task.done()`` is true by now and a state read taken on the back of the
    event agrees with it. Cancelled -> ``stopped`` (the Stop button), an
    exception -> ``failed``, otherwise ``done``.

    The same terminal word also clears the project row's "运行中" marker, so
    the row and the panel leave 运行中 together -- see :func:`_set_project_status`.
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
    _set_project_status(getattr(state, "project", None), state.status)
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
    state.project = project
    # The row reads project.status for its "运行中" badge; mirror the loop so
    # the row and the panel agree while the run is live (and the terminal word
    # replaces it in finalize_skeleton_run).
    _set_project_status(project, "running")
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


def _persisted_record_to_state(record: Dict[str, Any], *, run_file,
                               project_id: str = "") -> Dict[str, Any]:
    """Shape one persisted run record like :meth:`SkeletonState.to_dict`.

    A record on disk is a *finished* run (``run_task`` only writes it after the
    worker and verifier have run), so the lifecycle word is ``done``; whether it
    passed is the verdict's own word, carried through unchanged.
    """
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    verdict = record.get("verdict") if isinstance(record.get("verdict"), dict) else {}
    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    run_id = str(record.get("run_id") or "")
    try:
        ended_at = Path(run_file).stat().st_mtime
    except OSError:
        ended_at = 0.0
    return {
        "run_id": run_id,
        "session_id": run_id,
        "project_id": project_id,
        "workspace_kind": meta.get("workspace_kind", "") or "",
        "route": "skeleton",
        "route_source": "",            # not recoverable from the record
        "route_reason": "",
        "status": "done",
        "running": False,
        "outcome": record.get("outcome", "") or "",
        "passed": verdict.get("passed"),
        "verdict": verdict,
        "run_file": str(run_file),
        "artifacts": list(result.get("artifacts") or []),
        "error": "",
        "stop_requested": False,
        "started_at": ended_at,
        "ended_at": ended_at,
        "message": task.get("instruction", "") or "",
        #: Marks a state that came off disk rather than out of this process.
        "source": "record",
    }


def load_persisted_skeleton_state(root, project_id: str = "") -> Optional[Dict[str, Any]]:
    """Read the newest persisted skeleton run under ``root``; **read-only**.

    The live state (``project.skeleton_state``) exists only while the process
    that started the run is alive: a backend restart, or the project being
    re-hydrated from the DB, drops it -- and then a run that really finished
    (its record is on disk, ``GET /skeleton`` returned ``done`` a moment ago)
    read back as ``status="none"``. This reads that record instead, so a
    finished run survives the process.

    Strictly read-only: it lists the run directory and reads one JSON file; it
    never creates, deletes, or rewrites anything. Returns ``None`` when there is
    no readable record.
    """
    try:
        run_dir = Path(str(root)).expanduser() / RUN_DIR_REL
        if not run_dir.is_dir():
            return None
        candidates = [p for p in run_dir.glob(RUN_FILE_GLOB) if p.is_file()]
        if not candidates:
            return None
        # Newest first; skip an unreadable/corrupt one rather than giving up
        # (a half-written record from a crash mid-save must not hide the run).
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        logger.debug("no readable persisted skeleton run under %s", root,
                     exc_info=True)
        return None
    for newest in candidates:
        try:
            record = json.loads(newest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.debug("skipping unreadable skeleton record %s", newest,
                         exc_info=True)
            continue
        if isinstance(record, dict):
            return _persisted_record_to_state(record, run_file=newest,
                                              project_id=project_id)
    return None


def read_skeleton_state(project, root=None) -> Optional[Dict[str, Any]]:
    """The project's skeleton state as a plain dict, or ``None`` if it never ran.

    Live in-process state wins while the run is here. Once it is gone (restart,
    DB re-hydration) a *finished* run is still on disk, so read that back rather
    than reporting ``none`` -- the fallback is read-only.
    """
    state = getattr(project, "skeleton_state", None)
    if state is not None:
        return state.to_dict()
    if root is None:
        root = getattr(project, "work_dir", "") or getattr(project, "workspace", "")
    if not root:
        return None
    return load_persisted_skeleton_state(
        root, project_id=str(getattr(project, "id", "") or ""))


__all__ = [
    "SkeletonState",
    "start_skeleton_run",
    "stop_skeleton_run",
    "read_skeleton_state",
    "finalize_skeleton_run",
    "load_persisted_skeleton_state",
    "ENDED_TOPIC",
    "RUN_DIR_REL",
    "RUN_FILE_GLOB",
]
