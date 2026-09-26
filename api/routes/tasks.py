"""Tasks API — the app side of `kairos.har`, plus the background work already running.

Three kinds of work outlive a single chat turn in this project, and until now
only one of them was visible anywhere:

* **durable tasks** — the `.har/` contract from Round 29. `kairos/har.py` could
  create, resume and record one, and no code path outside its own CLI ever
  called it: "kick off a multi-day refactor, close the laptop, pick it up
  tomorrow" needed a terminal. `kairos/durable.py` binds it to the project's
  real loop; these routes expose it.
* **background subagents** — `spawn_subagent(background=true)` handles, held by
  the long-running registry and already reachable per handle.
* **autonomous jobs** — the registry's job records.

The list endpoint exists because the UI has nowhere to look otherwise: a handle
was only discoverable if you already knew it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _orch():
    """The live orchestrator (module attribute read at call time)."""
    from api.deps import orchestrator
    return orchestrator


class DurableBody(BaseModel):
    goal: str = ""
    rounds_planned: int = 10


class ResumeBody(BaseModel):
    ticks: int = 1


def _project(project_id: str):
    orchestrator = _orch()
    project = orchestrator.get_project(project_id) if orchestrator else None
    if project is None:
        raise HTTPException(404, "project not found: " + project_id)
    return project


def _background(project_id: str) -> dict:
    """Subagent handles + autonomous jobs, or empty lists if the registry is cold."""
    out: dict = {"subagents": [], "autonomous": []}
    try:
        from kairos.long_running import get_registry
        reg = get_registry()
        out["subagents"] = reg.list_subagents(project_id) or []
        out["autonomous"] = reg.list_autonomous(project_id) or []
    except Exception as e:  # noqa: BLE001
        logger.debug("background task listing failed: %s", e)
    return out


@router.get("/{project_id}")
async def tasks_for_project(project_id: str):
    """Everything this project has running or resumable, in one payload."""
    from kairos import durable

    project = _project(project_id)
    durable_state = durable.status(project)
    background = _background(project_id)
    return {
        "project_id": project_id,
        "durable": durable_state,
        "subagents": background["subagents"],
        "autonomous": background["autonomous"],
        "counts": {
            "durable": 1 if durable_state else 0,
            "subagents": len(background["subagents"]),
            "autonomous": len(background["autonomous"]),
        },
    }


@router.post("/{project_id}")
async def start_durable(project_id: str, body: DurableBody):
    """Start a durable task. One per project: `.har/` holds a single contract."""
    from kairos import durable

    project = _project(project_id)
    result = durable.start(project, body.goal, rounds_planned=body.rounds_planned)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "could not start the task")
    return result


@router.post("/{project_id}/resume")
async def resume_durable(project_id: str, body: ResumeBody):
    """Run the task for `ticks` rounds of the project's loop, saving after each.

    Returns `code` from the state layer: 4 = the loop approved the work and the
    task is done, 0 = ticks ran without approval, 2 = another resume holds the
    lock, 3 = no task exists.
    """
    from kairos import durable

    _project(project_id)
    result = await durable.resume(_orch(), project_id, ticks=body.ticks)
    if result.get("code") == 3:
        raise HTTPException(404, result.get("message") or "no durable task")
    return result


@router.get("/{project_id}/history")
async def durable_history(project_id: str, limit: int = 50):
    """The `.har` JSONL history, newest last."""
    from kairos import durable, har

    project = _project(project_id)
    if not durable.is_durable(project):
        raise HTTPException(404, "no durable task for this project")
    _contract, _state, h = har.load_har(durable.task_root(project))
    return {"project_id": project_id,
            "history": har.read_history(h, limit=max(1, int(limit)))}
