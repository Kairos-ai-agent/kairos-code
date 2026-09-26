"""Artifacts — what a run produces, kept as a thing with an address.

The competitor comparison that drove R2 and R3 put Antigravity's *Artifacts* next
to our loop's evidence chain, and the honest reading of the gap was not "we have
no evidence". We have the same objects; we throw most of them away as strings in
a transcript. A round's plan lands in `.har/plan.md`, a gate report can be
rendered on demand, a screenshot the browser tool takes is written under the data
directory — and none of them is addressable, so none of them can be reopened,
linked, or commented on.

An artifact here is deliberately small: a row (in the project's database; the
schema lives with every other table, in `kairos/core/persistence.py`) with a
kind, a title, a body or a file path, and a comment thread.

This module is the writing side: the loop records a plan or a round summary, the
browser tool records a screenshot, and the gate report records itself. Every
entry point is **best effort**: an artifact that cannot be saved must not fail
the round that produced it, but it must also not disappear silently — hence the
warning, and `None` rather than a fake record.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Deliberately a closed set: the UI groups by kind, and a free-form string would
# turn "what has this project produced?" into a list of typos.
KIND_PLAN = "plan"
KIND_REPORT = "report"
KIND_SCREENSHOT = "screenshot"
KIND_SUMMARY = "summary"
KIND_NOTE = "note"
KINDS = (KIND_PLAN, KIND_REPORT, KIND_SCREENSHOT, KIND_SUMMARY, KIND_NOTE)

# Bodies can be long (a plan, a rendered report) but a row should stay a row.
MAX_BODY = 200_000


@dataclass
class Artifact:
    """One produced thing."""

    project_id: str
    kind: str
    title: str
    body: str = ""
    path: str = ""
    session_id: str = ""
    round_no: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "path": self.path,
            "session_id": self.session_id,
            "round_no": self.round_no,
            "created_at": self.created_at,
            "meta": dict(self.meta),
        }


def _db():
    """The project database, or None when nothing has opened one yet.

    Read through the orchestrator (which owns the connection) rather than
    building a second one: two `Persistence` objects on one SQLite file is how
    "it saved, but the page is empty" starts.
    """
    try:
        from api.deps import orchestrator
    except Exception:  # noqa: BLE001
        return None
    if orchestrator is None:
        return None
    return getattr(orchestrator, "_db", None) or getattr(orchestrator, "db", None)


def record(kind: str, title: str, *, project_id: str, body: str = "",
           path: str = "", session_id: str = "", round_no: int = 0,
           meta: Optional[Dict[str, Any]] = None,
           db: Any = None) -> Optional[Artifact]:
    """Save one artifact. Returns None when there is nowhere to save it."""
    if kind not in KINDS:
        raise ValueError("unknown artifact kind: " + str(kind))
    if not project_id:
        logger.debug("artifact %r has no project; not recording", title)
        return None

    store = db if db is not None else _db()
    if store is None:
        logger.debug("no database open; artifact %r kept in memory only", title)
        return None

    artifact = Artifact(project_id=project_id, kind=kind, title=title,
                        body=body[:MAX_BODY], path=str(path or ""),
                        session_id=session_id, round_no=int(round_no or 0),
                        meta=dict(meta or {}))
    try:
        store.add_artifact(artifact.to_dict())
    except Exception as exc:  # noqa: BLE001
        # Never fail a round because its receipt could not be filed.
        logger.warning("could not record artifact %r: %s", title, exc)
        return None
    return artifact


# ---------------------------------------------------------------------------
# The producers, named for what they are
# ---------------------------------------------------------------------------

def record_plan(project_id: str, plan_text: str, *, session_id: str = "",
                round_no: int = 0, db: Any = None) -> Optional[Artifact]:
    """A round's plan — the same text that goes to `.har/plan.md`."""
    if not (plan_text or "").strip():
        return None
    return record(KIND_PLAN, "Round " + str(round_no) + " plan" if round_no
                  else "Plan", project_id=project_id, body=plan_text,
                  session_id=session_id, round_no=round_no,
                  meta={"chars": len(plan_text)}, db=db)


def record_summary(project_id: str, summary: str, *, session_id: str = "",
                   round_no: int = 0, approved: Optional[bool] = None,
                   score: Optional[int] = None, db: Any = None):
    """What a round concluded, with the verdict attached."""
    if not (summary or "").strip():
        return None
    return record(KIND_SUMMARY, "Round " + str(round_no) + " result" if round_no
                  else "Result", project_id=project_id, body=summary,
                  session_id=session_id, round_no=round_no,
                  meta={"approved": approved, "score": score}, db=db)


def record_screenshot(project_id: str, path: Path, *, url: str = "",
                      session_id: str = "", round_no: int = 0,
                      db: Any = None) -> Optional[Artifact]:
    """A screenshot the browser tool took: the file is the artifact."""
    return record(KIND_SCREENSHOT, "Screenshot", project_id=project_id,
                  path=str(path), session_id=session_id, round_no=round_no,
                  meta={"url": url, "filename": Path(path).name}, db=db)


def record_report(project_id: str, title: str, body: str, *, fmt: str = "md",
                  session_id: str = "", round_no: int = 0,
                  db: Any = None) -> Optional[Artifact]:
    """A rendered report (the gate report is the one that exists today)."""
    return record(KIND_REPORT, title, project_id=project_id, body=body,
                  session_id=session_id, round_no=round_no,
                  meta={"format": fmt}, db=db)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def list_for_project(project_id: str, *, kind: str = "", limit: int = 50,
                     db: Any = None) -> List[Dict[str, Any]]:
    store = db if db is not None else _db()
    if store is None:
        return []
    try:
        return store.list_artifacts(project_id, kind=kind,
                                    limit=max(1, int(limit))) or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not list artifacts: %s", exc)
        return []


def get(artifact_id: str, *, db: Any = None) -> Optional[Dict[str, Any]]:
    store = db if db is not None else _db()
    if store is None:
        return None
    try:
        return store.get_artifact(artifact_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read artifact %s: %s", artifact_id, exc)
        return None


def comments(artifact_id: str, *, limit: int = 100, db: Any = None) -> List[Dict[str, Any]]:
    store = db if db is not None else _db()
    if store is None:
        return []
    try:
        return store.list_artifact_comments(artifact_id, limit=limit) or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not list comments: %s", exc)
        return []


def add_comment(artifact_id: str, body: str, *, author: str = "user",
                db: Any = None) -> Optional[Dict[str, Any]]:
    """A comment on an artifact: the reason artifacts are addressable at all."""
    text = (body or "").strip()
    if not text:
        raise ValueError("a comment needs a body")
    store = db if db is not None else _db()
    if store is None:
        return None
    try:
        return store.add_artifact_comment(artifact_id, author, text[:MAX_BODY])
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record a comment on %s: %s", artifact_id, exc)
        return None
