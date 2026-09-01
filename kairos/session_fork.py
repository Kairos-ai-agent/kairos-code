"""Session Fork — branch from any checkpoint (the multi-model CLI-style).

R38.6 §34: the multi-model CLI lets you spin off a parallel experiment
from any prior session state via "session fork". We expose
the same capability on top of the existing workbench
checkpoint/restore.

Use case:
  1. You're mid-loop, realize the agent is going down a
     wrong path.
  2. Click "Fork from last checkpoint" — restores the
     files to that snapshot AND gives you a fresh session
     so you can redirect the agent without losing the
     failed run as a record.

Storage:
  - Forks live in the project's
    ``.kairos/forks/<fork_id>.json`` so they're per-project.
  - The actual file content lives in the same
    ``.kairos/workbench/`` checkpoints the Restore button
    uses — we don't duplicate file content, we just point
    the fork at a snapshot id.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Fork:
    """A forked session that branched from a checkpoint."""
    id: str
    project_id: str
    parent_session_id: str          # the loop_session this forked from
    source_snapshot_id: str         # the checkpoint to restore from
    label: str                       # user-given name
    created_at: float = field(default_factory=time.time)
    # The new session id to use for the next /chat or /start
    new_session_id: Optional[str] = None
    # Optional note
    note: str = ""


class ForkStore:
    """Per-project store for fork records."""

    def __init__(self, work_dir: Path):
        self.root = Path(work_dir) / ".kairos" / "forks"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, fork_id: str) -> Path:
        return self.root / f"{fork_id}.json"

    def save(self, fork: Fork) -> None:
        with open(self._path(fork.id), "w", encoding="utf-8") as f:
            json.dump(asdict(fork), f, ensure_ascii=False, indent=2)

    def get(self, fork_id: str) -> Optional[Fork]:
        p = self._path(fork_id)
        if not p.exists():
            return None
        try:
            return Fork(**json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("fork %s read failed: %s", fork_id, exc)
            return None

    def list_for_project(self, project_id: str) -> List[Fork]:
        out: List[Fork] = []
        for f in self.root.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("project_id") == project_id:
                    out.append(Fork(**d))
            except (json.JSONDecodeError, TypeError, OSError):
                continue
        out.sort(key=lambda f: f.created_at, reverse=True)
        return out

    def delete(self, fork_id: str) -> bool:
        p = self._path(fork_id)
        if p.exists():
            p.unlink()
            return True
        return False


def create_fork(project_id: str, parent_session_id: str,
                 snapshot_id: str, label: str = "",
                 note: str = "") -> Fork:
    """Create a fork record. Caller is responsible for
    invoking the workbench /restore to actually rewind
    the files (or leaving them in place)."""
    fork = Fork(
        id=uuid.uuid4().hex[:12],
        project_id=project_id,
        parent_session_id=parent_session_id,
        source_snapshot_id=snapshot_id,
        label=label or f"Fork from {snapshot_id[:8]}",
        new_session_id=uuid.uuid4().hex[:12],
        note=note,
    )
    return fork
