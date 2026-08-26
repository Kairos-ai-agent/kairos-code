"""Three-tier memory hierarchy.

Following Claude Code's model: memories are scoped to a
*lifetime*, and each tier is queried / persisted differently:

  - **user** — follows the user across *all* projects. Persisted
    to ``<data_dir>/memory/user.json``. Examples: "user
    prefers dark mode", "user's name is Alice".

  - **project** — bound to one project. Persisted alongside the
    project DB. Examples: "this repo uses Black", "CI runs on
    GitHub Actions".

  - **session** — ephemeral, lives only in the running
    ``LoopSession``. Examples: "current task is to add a benchmark",
    "we already discussed Auth earlier".

The :class:`MemoryHierarchy` ties these three together. It
implements the same ``read / write / query`` interface that
:class:`kairos.learning.auto_memory.AutoMemory` already exposes
for the *project* tier, so the rest of the codebase doesn't
need to know which tier a record lives in.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


DEFAULT_USER_PATH = "~/.kairos/memory/user.json"


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass
class MemoryRecord:
    """One piece of memory, tagged with its tier."""
    id: str
    tier: str  # "user" | "project" | "session"
    category: str  # "always" | "never" | "context" | etc.
    content: str
    project_id: str = ""
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        d = dict(d)
        d.setdefault("tier", "project")
        d.setdefault("category", "context")
        d.setdefault("content", "")
        d.setdefault("metadata", {})
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Tier-specific persistence
# ---------------------------------------------------------------------------


def _user_path(custom: str = "") -> Path:
    if custom:
        return Path(custom).expanduser()
    return Path(os.path.expanduser(DEFAULT_USER_PATH))


def _project_path(custom: str, project_id: str) -> Path:
    if custom:
        # Substitute {id} with the project_id, then expand ~
        return Path(custom.replace("{id}", project_id)).expanduser()
    return Path(os.path.expanduser(f"~/.kairos/memory/projects/{project_id}.json"))


def _load_json(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("memory: failed to load %s: %s", path, exc)
        return []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _save_json(path: Path, records: List[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except OSError as exc:
        logger.warning("memory: failed to save %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class MemoryHierarchy:
    """Read/write memory across the three tiers.

    Reads are unioned (user + project + session), with the tier
    priority: session > project > user (so session notes
    override project facts for the current run, project facts
    override user defaults). Writes go to exactly one tier.
    """

    def __init__(self, user_path: str = "", project_path: str = "") -> None:
        self._lock = threading.Lock()
        self._user_path = user_path
        self._project_path_template = project_path  # formatted with project_id
        self._session: List[MemoryRecord] = []

    # -- session tier ----------------------------------------------------

    def session_add(self, content: str, category: str = "context",
                    project_id: str = "", session_id: str = "",
                    metadata: Optional[dict] = None) -> MemoryRecord:
        rec = MemoryRecord(
            id=os.urandom(8).hex(),
            tier="session",
            category=category,
            content=content,
            project_id=project_id,
            session_id=session_id,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._session.append(rec)
        return rec

    def session_clear(self) -> int:
        with self._lock:
            n = len(self._session)
            self._session.clear()
        return n

    # -- project tier ----------------------------------------------------

    def project_add(self, project_id: str, content: str,
                    category: str = "context",
                    metadata: Optional[dict] = None) -> MemoryRecord:
        rec = MemoryRecord(
            id=os.urandom(8).hex(),
            tier="project",
            category=category,
            content=content,
            project_id=project_id,
            metadata=dict(metadata or {}),
        )
        path = _project_path(self._project_path_template, project_id)
        records = _load_json(path)
        records.append(rec.to_dict())
        _save_json(path, records)
        return rec

    def project_list(self, project_id: str) -> List[MemoryRecord]:
        path = _project_path(self._project_path_template, project_id)
        return [MemoryRecord.from_dict(r) for r in _load_json(path)]

    # -- user tier -------------------------------------------------------

    def user_add(self, content: str, category: str = "always",
                 metadata: Optional[dict] = None) -> MemoryRecord:
        rec = MemoryRecord(
            id=os.urandom(8).hex(),
            tier="user",
            category=category,
            content=content,
            metadata=dict(metadata or {}),
        )
        path = _user_path(self._user_path)
        records = _load_json(path)
        records.append(rec.to_dict())
        _save_json(path, records)
        return rec

    def user_list(self) -> List[MemoryRecord]:
        path = _user_path(self._user_path)
        return [MemoryRecord.from_dict(r) for r in _load_json(path)]

    # -- read (union across all three tiers) -----------------------------

    def recall(self, project_id: str = "", session_id: str = "",
               category: Optional[str] = None) -> List[MemoryRecord]:
        """Return all matching records, tier-priority order:
        session > project > user. ``category`` filters to a single
        category (e.g. ``"always"`` for things the agent must do)."""
        out: List[MemoryRecord] = []
        with self._lock:
            session = list(self._session)
        for r in session:
            if r.project_id and project_id and r.project_id != project_id:
                continue
            if r.session_id and session_id and r.session_id != session_id:
                continue
            if category and r.category != category:
                continue
            out.append(r)
        for r in self.project_list(project_id):
            if category and r.category != category:
                continue
            out.append(r)
        for r in self.user_list():
            if category and r.category != category:
                continue
            out.append(r)
        return out

    def recall_always(self, project_id: str = "") -> List[str]:
        """Shortcut: only the ``always``-category records (things
        the agent must always do). Returns just the content."""
        return [r.content for r in self.recall(project_id=project_id,
                                               category="always")]

    def recall_never(self, project_id: str = "") -> List[str]:
        return [r.content for r in self.recall(project_id=project_id,
                                               category="never")]

    # -- stats ------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        with self._lock:
            session = len(self._session)
        try:
            user = len(self.user_list())
        except Exception:
            user = 0
        return {
            "session_records": session,
            "user_records": user,
            # project count is per-project; we just report 0 here
            "project_records": -1,  # -1 = unknown without a project_id
        }
