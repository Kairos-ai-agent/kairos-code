"""Inbound task entry — start a project from a GitHub issue or a Linear issue.

Codex can open a task from GitHub, GitLab, Linear or Slack; our inbound surface
was a Feishu webhook and nothing else. This is the missing direction: an issue
becomes a project, with no browser in the loop.

Three things this file refuses to do, because a webhook is an unauthenticated
door until proven otherwise:

* **Refuse unsigned deliveries.** Every request is verified against a shared
  secret with a constant-time compare. If no secret is configured the endpoint
  answers 503 rather than accepting anything.
* **Refuse silently.** A signature mismatch is a 401. An event we do not act on
  is a 200 with `created: false` and a reason, so the provider stops retrying.
* **Refuse to duplicate.** The project list is the ledger: the external issue id
  is written into the project description as `[github#123]`, and a second
  delivery for the same issue finds it. A redelivery is not a second project.

Secrets come from the environment — `KAIROS_HOOK_GITHUB_SECRET` and
`KAIROS_HOOK_LINEAR_SECRET` — so they never sit in a config file inside a repo
that people are told to copy around. `KAIROS_HOOK_GITHUB_LABEL` is optional: set
it and only issues carrying that label are accepted.

What this deliberately does not do is start the loop. Creating a project is
reversible; spending money and writing files is not, and the person who opened
the issue did not necessarily ask for that. `/api/loop/start` is one click, and
the Tasks screen shows what is queued.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hooks", tags=["hooks"])

GITHUB_SECRET_ENV = "KAIROS_HOOK_GITHUB_SECRET"
GITHUB_LABEL_ENV = "KAIROS_HOOK_GITHUB_LABEL"
LINEAR_SECRET_ENV = "KAIROS_HOOK_LINEAR_SECRET"


def _signature_ok(secret: str, body: bytes, provided: str, algo: str = "sha256") -> bool:
    """Constant-time HMAC check. `provided` may carry a `sha256=` prefix."""
    if not secret or not provided:
        return False
    candidate = provided.split("=", 1)[1] if "=" in provided else provided
    digest = hmac.new(secret.encode("utf-8"), body, getattr(hashlib, algo))
    return hmac.compare_digest(digest.hexdigest(), candidate.strip().lower())


def _existing_project(marker: str) -> Optional[str]:
    """Find the project this issue already became, if any."""
    from api import deps

    orchestrator = deps.orchestrator
    if orchestrator is None:
        return None
    try:
        projects = orchestrator.list_projects()
    except Exception:  # noqa: BLE001 - a missing project list is not a 500
        return None
    for project in projects or []:
        description = str(getattr(project, "description", "") or "")
        if marker in description:
            return str(getattr(project, "id", "") or "")
    return None


def _create(marker: str, name: str, description: str) -> Dict[str, Any]:
    from api import deps

    orchestrator = deps.orchestrator
    if orchestrator is None:
        raise HTTPException(503, "no orchestrator is open in this process")

    existing = _existing_project(marker)
    if existing:
        return {"created": False, "project_id": existing, "reason": "already tracked"}

    body = "%s\n\n(%s)" % (description.strip(), marker)
    project = orchestrator.create_project(name=name[:200] or "Untitled issue",
                                         description=body)
    project_id = str(getattr(project, "id", "") or "")
    logger.info("inbound hook created project %s (%s)", project_id, marker)
    return {"created": True, "project_id": project_id, "reason": "created"}


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _github_issue(payload: Dict[str, Any]) -> Tuple[Optional[str], str, str]:
    """(marker, name, description) for an actionable `issues` delivery."""
    issue = payload.get("issue") or {}
    number = issue.get("number")
    repo = (payload.get("repository") or {}).get("full_name") or "github"
    if number is None:
        return None, "", ""
    title = str(issue.get("title") or "").strip()
    url = str(issue.get("html_url") or "").strip()
    text = str(issue.get("body") or "").strip()
    who = (issue.get("user") or {}).get("login") or "someone"
    description = "%s\n\n%s\n\nFrom %s in %s: %s" % (
        title, text[:4000], who, repo, url)
    return "[github#%s]" % number, title or ("%s#%s" % (repo, number)), description


@router.get("/status")
async def hooks_status() -> Dict[str, Any]:
    """Which doors are actually open, and where to paste them.

    Read-only on purpose: "is this configured" is the question people ask when a
    webhook does not fire, and answering it should not require reading the
    environment from a shell. Secrets are reported as set or not set — never
    echoed, never truncated, never a length.
    """
    return {
        "sources": [
            {"name": "github", "path": "/api/hooks/github",
             "configured": bool(os.environ.get(GITHUB_SECRET_ENV)),
             "secret_env": GITHUB_SECRET_ENV,
             "label_filter": os.environ.get(GITHUB_LABEL_ENV, "") or None,
             "events": ["issues"]},
            {"name": "linear", "path": "/api/hooks/linear",
             "configured": bool(os.environ.get(LINEAR_SECRET_ENV)),
             "secret_env": LINEAR_SECRET_ENV,
             "label_filter": None,
             "events": ["Issue"]},
        ],
        "starts_the_loop": False,
        "note": ("A delivery creates a project. Starting the loop stays a human "
                 "decision — see /api/loop/start."),
    }


@router.post("/github")
async def github_hook(request: Request,
                      x_hub_signature_256: str = Header(default=""),
                      x_github_event: str = Header(default="")) -> Dict[str, Any]:
    secret = os.environ.get(GITHUB_SECRET_ENV, "")
    if not secret:
        raise HTTPException(503, "%s is not set; refusing unsigned deliveries"
                            % GITHUB_SECRET_ENV)

    raw = await request.body()
    if not _signature_ok(secret, raw, x_hub_signature_256):
        raise HTTPException(401, "signature does not match")

    if x_github_event and x_github_event != "issues":
        return {"created": False, "reason": "ignoring %s events" % x_github_event}

    payload = await request.json()
    action = payload.get("action")
    if action not in ("opened", "reopened", "labeled"):
        return {"created": False, "reason": "ignoring action %r" % action}

    wanted = os.environ.get(GITHUB_LABEL_ENV, "").strip().lower()
    if wanted:
        labels = [str((l or {}).get("name", "")).lower()
                  for l in (payload.get("issue") or {}).get("labels") or []]
        if wanted not in labels:
            return {"created": False, "reason": "no %s label" % wanted}

    marker, name, description = _github_issue(payload)
    if not marker:
        return {"created": False, "reason": "no issue in payload"}
    return _create(marker, name, description)


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------

def _linear_issue(payload: Dict[str, Any]) -> Tuple[Optional[str], str, str]:
    data = payload.get("data") or {}
    identifier = data.get("identifier") or data.get("id")
    if not identifier:
        return None, "", ""
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    url = str(data.get("url") or "").strip()
    team = ((data.get("team") or {}).get("name")) or "Linear"
    text = "%s\n\n%s\n\nFrom %s: %s" % (title, description[:4000], team, url)
    return "[linear#%s]" % identifier, title or str(identifier), text


@router.post("/linear")
async def linear_hook(request: Request,
                      linear_signature: str = Header(default="")) -> Dict[str, Any]:
    secret = os.environ.get(LINEAR_SECRET_ENV, "")
    if not secret:
        raise HTTPException(503, "%s is not set; refusing unsigned deliveries"
                            % LINEAR_SECRET_ENV)

    raw = await request.body()
    if not _signature_ok(secret, raw, linear_signature):
        raise HTTPException(401, "signature does not match")

    payload = await request.json()
    if str(payload.get("type") or "").lower() != "issue":
        return {"created": False, "reason": "ignoring %s data" % payload.get("type")}
    if str(payload.get("action") or "").lower() not in ("create", "update"):
        return {"created": False,
                "reason": "ignoring action %r" % payload.get("action")}

    marker, name, description = _linear_issue(payload)
    if not marker:
        return {"created": False, "reason": "no issue in payload"}
    return _create(marker, name, description)
