"""Session resume and fork helpers.

A "session" in Kairos is a single execution run of a project: the
moment the user clicks Start (or ``kairos exec`` is invoked) until
the loop converges or the user stops it. The orchestrator already
persists everything we need (projects, messages, loop rounds,
checkpoints) to SQLite via ``Persistence``; this module just gives
that data a clean public surface so:

  - ``list_sessions()`` enumerates known sessions for a project
  - ``resume()`` produces a project + ordered history that the
    caller can use to rebuild an agent's context
  - ``fork()`` duplicates a project into a new one (so a run can
    branch without losing the original)

The heavy lifting is delegated to ``Persistence`` so this module
stays a thin layer that can be unit-tested without standing up an
Orchestrator.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """A short description of one past session."""
    project_id: str
    session_id: str
    started_at: float
    last_message_at: float
    rounds: int
    approved_rounds: int
    messages: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "last_message_at": self.last_message_at,
            "rounds": self.rounds,
            "approved_rounds": self.approved_rounds,
            "messages": self.messages,
        }


@dataclass
class ResumedSession:
    """A snapshot of a session's history, suitable for restoring
    agent memory or seeding a UI's "Resume from here" button."""
    project: Dict[str, Any]
    history: List[Dict[str, Any]] = field(default_factory=list)
    loop_rounds: List[Dict[str, Any]] = field(default_factory=list)
    checkpoints: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "history": self.history,
            "loop_rounds": self.loop_rounds,
            "checkpoints": self.checkpoints,
        }


class SessionManager:
    """Thin wrapper around Persistence for session-level operations.

    A "session" is identified by a (project_id, session_id) pair.
    session_id is recorded in the messages and loop_rounds tables
    so we can pull a coherent timeline later.
    """

    def __init__(self, persistence):
        # Accept any object that quacks like Persistence. We avoid
        # importing the concrete class so this module can be
        # unit-tested in isolation.
        self._db = persistence

    # -- enumeration -----------------------------------------------------

    def list_sessions(self, project_id: str) -> List[SessionInfo]:
        """Return all sessions ever run on `project_id`, newest first.

        A "session" is grouped by the `session_id` field embedded in
        messages / loop_rounds. Sessions with zero recorded activity
        (i.e. the project never ran) are NOT listed — use
        ``persistence.load_projects()`` to enumerate those.
        """
        sessions: Dict[str, Dict[str, Any]] = {}
        # Walk messages first; they always carry a timestamp.
        for msg in self._db.load_messages(limit=10_000, project_id=project_id):
            sid = (msg.get("metadata") or {}).get("session_id") or "_default"
            s = sessions.setdefault(sid, {
                "started_at": msg["timestamp"] or 0.0,
                "last_message_at": msg["timestamp"] or 0.0,
                "messages": 0,
                "rounds": 0,
                "approved_rounds": 0,
            })
            s["messages"] += 1
            ts = msg.get("timestamp") or 0.0
            if ts < s["started_at"] or not s["started_at"]:
                s["started_at"] = ts
            if ts > s["last_message_at"]:
                s["last_message_at"] = ts
        for r in self._db.load_loop_rounds(project_id, limit=10_000):
            sid = r.get("session_id") or "_default"
            s = sessions.setdefault(sid, {
                "started_at": r.get("ts") or 0.0,
                "last_message_at": r.get("ts") or 0.0,
                "messages": 0,
                "rounds": 0,
                "approved_rounds": 0,
            })
            s["rounds"] += 1
            if r.get("approved"):
                s["approved_rounds"] += 1
        # Newest first.
        out = [
            SessionInfo(
                project_id=project_id,
                session_id=sid,
                started_at=s["started_at"],
                last_message_at=s["last_message_at"],
                rounds=s["rounds"],
                approved_rounds=s["approved_rounds"],
                messages=s["messages"],
            )
            for sid, s in sessions.items()
        ]
        out.sort(key=lambda x: x.last_message_at, reverse=True)
        return out

    # -- resume ----------------------------------------------------------

    def resume(
        self,
        project_id: str,
        session_id: Optional[str] = None,
        history_limit: int = 200,
    ) -> Optional[ResumedSession]:
        """Build a ResumedSession snapshot.

        If `session_id` is None, returns the most recent session.
        """
        projects = self._db.load_projects()
        project = next(
            (p for p in projects if p.get("id") == project_id),
            None,
        )
        if project is None:
            return None
        # History: messages scoped to project_id (and optionally
        # session_id, but we keep it loose here — the caller can
        # filter further if they need to).
        history = self._db.load_messages(
            limit=history_limit, project_id=project_id
        )
        loop_rounds = self._db.load_loop_rounds(project_id, limit=history_limit)
        checkpoints = self._db.load_checkpoints(project_id, limit=history_limit)
        if session_id:
            history = [
                m for m in history
                if (m.get("metadata") or {}).get("session_id") == session_id
            ]
            loop_rounds = [
                r for r in loop_rounds if r.get("session_id") == session_id
            ]
            checkpoints = [
                c for c in checkpoints if c.get("session_id") == session_id
            ]
        else:
            # No explicit session_id: pick the most recent session
            # and filter everything to that one. Otherwise we mix
            # messages from multiple sessions and the snapshot is
            # incoherent.
            sessions = self.list_sessions(project_id)
            if sessions:
                latest = sessions[0].session_id
                history = [
                    m for m in history
                    if (m.get("metadata") or {}).get("session_id") == latest
                ]
                loop_rounds = [
                    r for r in loop_rounds if r.get("session_id") == latest
                ]
                checkpoints = [
                    c for c in checkpoints if c.get("session_id") == latest
                ]
        return ResumedSession(
            project=project,
            history=history,
            loop_rounds=loop_rounds,
            checkpoints=checkpoints,
        )

    # -- fork ------------------------------------------------------------

    def fork(
        self,
        source_project_id: str,
        new_name: Optional[str] = None,
        work_dir: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Duplicate a project into a fresh one. Returns the new
        project record, or None if the source doesn't exist.

        Fork copies the project's metadata (name/description/etc.)
        and bumps the work_dir to a fresh path. It does NOT copy
        message history or loop rounds — those belong to the
        original session; the fork is a clean slate you can run
        a different direction from.

        Worktree / branch fields are intentionally left to the
        caller. To fork into a worktree, the caller should pass
        a work_dir that lives inside an existing worktree (the
        WorktreeManager handles the git branch side).
        """
        from kairos.core.orchestrator import Project  # local import to avoid cycles

        projects = self._db.load_projects()
        src = next(
            (p for p in projects if p.get("id") == source_project_id),
            None,
        )
        if src is None:
            return None
        new_id = uuid.uuid4().hex[:8]
        new_record = {
            "id": new_id,
            "name": new_name or f"{src.get('name', 'project')} (fork)",
            "description": src.get("description", ""),
            "workspace": src.get("workspace", ""),
            "work_dir": work_dir or src.get("work_dir", ""),
            "requirements": src.get("requirements", ""),
            "status": "active",
            "created_at": time.time(),
        }
        # Persist via Persistence.save_project (expects a Project
        # object). We instantiate a minimal Project; Orchestrator's
        # add_later flow will populate agents/loop machinery.
        try:
            project_obj = Project(
                id=new_record["id"],
                name=new_record["name"],
                description=new_record["description"],
                workspace=Path(new_record["workspace"]) if new_record["workspace"] else None,
                work_dir=new_record["work_dir"],
                db=self._db,
            )
        except TypeError:
            # Project may have a different signature in this
            # version; fall back to a bare-bones call that just
            # touches the fields the SQL layer cares about.
            project_obj = Project.__new__(Project)
            for k, v in new_record.items():
                setattr(project_obj, k, v)
        try:
            self._db.save_project(project_obj)
        except Exception as exc:
            logger.error("session.fork: save_project failed: %s", exc)
            return None
        return new_record


# Convenience: when the orchestrator wants a "current session id"
# it can call this and stash the result on the loop session.
def new_session_id() -> str:
    """Mint a fresh session id (`sess-xxxxxxxx`)."""
    return f"sess-{uuid.uuid4().hex[:8]}"
