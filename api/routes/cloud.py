"""HTTP API for cloud delegation.

Endpoints (mounted under /api/projects/{project_id}/cloud by app.py):

    POST   /cloud/submit          — submit a delegation task
    GET    /cloud/{task_id}       — fetch current result
    POST   /cloud/{task_id}/wait  — block until terminal, return final
    POST   /cloud/{task_id}/cancel — cancel a queued/running task
    GET    /cloud                 — list known delegators + their tasks

The delegator is process-local: configured at startup from
KAIROS_CLOUD_URL / KAIROS_CLOUD_OFFLINE env vars, or registered
programmatically via `register_delegator()`.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from kairos.cloud import (
    CloudDelegator,
    DelegationError,
    DelegationRequest,
    DelegationResult,
    DelegationStatus,
    LocalDelegator,
    make_default_delegator,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Project_id -> delegator instance. Populated by the startup hook.
_DELEGATORS: Dict[str, Any] = {}
_DELEGATORS_LOCK = threading.Lock()


def register_delegator(project_id: str, delegator: Any) -> None:
    with _DELEGATORS_LOCK:
        _DELEGATORS[project_id] = delegator


def get_delegator(project_id: str) -> Optional[Any]:
    with _DELEGATORS_LOCK:
        return _DELEGATORS.get(project_id)


def clear_delegator(project_id: str) -> None:
    with _DELEGATORS_LOCK:
        _DELEGATORS.pop(project_id, None)


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class SubmitRequest(BaseModel):
    description: str = Field(..., min_length=1)
    work_dir: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    callback_url: str = ""


class SubmitResponse(BaseModel):
    project_id: str
    task_id: str
    status: str


class WaitRequest(BaseModel):
    timeout_seconds: int = 300


class StatusResponse(BaseModel):
    project_id: str
    task_id: str
    status: str
    output: str = ""
    error: str = ""
    files_changed: List[str] = Field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/{project_id}/cloud/submit", response_model=SubmitResponse)
async def submit_task(project_id: str, request: SubmitRequest):
    delegator = get_delegator(project_id)
    if delegator is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"no delegator configured for project {project_id}. "
                "Set KAIROS_CLOUD_URL or call register_delegator()."
            ),
        )
    req = DelegationRequest(
        task_id="",  # server will assign
        project_id=project_id,
        description=request.description,
        work_dir=request.work_dir,
        context=dict(request.context),
        attachments=list(request.attachments),
        callback_url=request.callback_url,
    )
    try:
        task_id = delegator.submit(req)
    except DelegationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return SubmitResponse(
        project_id=project_id, task_id=task_id,
        status=DelegationStatus.QUEUED.value,
    )


@router.get("/{project_id}/cloud/{task_id}", response_model=StatusResponse)
async def get_status(project_id: str, task_id: str):
    delegator = get_delegator(project_id)
    if delegator is None:
        raise HTTPException(status_code=503,
                            detail=f"no delegator for {project_id}")
    try:
        result = delegator.get_result(task_id)
    except DelegationError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _result_to_response(project_id, result)


@router.post("/{project_id}/cloud/{task_id}/wait", response_model=StatusResponse)
async def wait_until_done(project_id: str, task_id: str,
                          request: WaitRequest = WaitRequest()):
    delegator = get_delegator(project_id)
    if delegator is None:
        raise HTTPException(status_code=503,
                            detail=f"no delegator for {project_id}")
    # For HTTP-based delegators, `poll_until_done` is blocking and
    # doesn't accept a timeout. We rely on the delegator's own
    # `max_polls * poll_interval` ceiling.
    try:
        result = delegator.poll_until_done(task_id)
    except DelegationError as e:
        raise HTTPException(status_code=504, detail=str(e))
    return _result_to_response(project_id, result)


@router.post("/{project_id}/cloud/{task_id}/cancel")
async def cancel_task(project_id: str, task_id: str):
    delegator = get_delegator(project_id)
    if delegator is None:
        raise HTTPException(status_code=503,
                            detail=f"no delegator for {project_id}")
    try:
        delegator.cancel(task_id)
    except DelegationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"cancelled": task_id}


@router.get("/{project_id}/cloud")
async def list_delegators(project_id: str):
    """Inspect which delegator is configured for the project.

    Returns the type and a few non-secret config fields so the UI
    can show "delegation: cloud @ https://..." or "delegation: local".
    """
    delegator = get_delegator(project_id)
    if delegator is None:
        return {"project_id": project_id, "configured": False}
    info: Dict[str, Any] = {
        "project_id": project_id,
        "configured": True,
        "type": type(delegator).__name__,
    }
    if isinstance(delegator, CloudDelegator):
        info["base_url"] = delegator.base_url
        info["timeout_s"] = delegator.timeout_s
        info["max_polls"] = delegator.max_polls
        info["poll_interval"] = delegator.poll_interval
        # Don't leak the auth token.
    elif isinstance(delegator, LocalDelegator):
        info["simulate_latency"] = delegator.simulate_latency
    return info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result_to_response(project_id: str,
                        result: DelegationResult) -> StatusResponse:
    return StatusResponse(
        project_id=project_id,
        task_id=result.task_id,
        status=result.status.value,
        output=result.output,
        error=result.error,
        files_changed=list(result.files_changed),
        started_at=result.started_at,
        finished_at=result.finished_at,
    )
