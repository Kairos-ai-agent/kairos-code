"""Auto-checkpoint before agent file writes (R38.6 §30).

The user reported "checkpoint should be automatic, not just
manual." Until now, snapshots were only created when the
user clicked "Snapshot now" in the right-side Workbench
panel — the agent had no way to recover if it overwrote a
file mid-loop and the user wanted to roll back.

This module is the agent-side counterpart to the
``/api/workbench/checkpoint`` endpoint. It lives in-process
so the file tools can call it without an HTTP roundtrip,
and so the back-up data is captured in the same Python
process that's about to overwrite the file (no race with
the backend being down).

Storage
-------

Back-ups are written to::

    <work_dir>/.kairos/autocheckpoints/<timestamp>/<rel_path>

Same hidden-dir convention as the manual checkpoints
(``workbench``). A cap (default: keep last 20) prevents
disk from filling up over a long session.

Lifecycle
---------

1. ``AutoCheckpointer(project_dir=...)`` is created once per
   agent (or per project). It holds the project root.

2. ``before_write(rel_path)`` is called by FileEditTool /
   FileEditReplaceTool / MultiEditTool right before the
   ``write_text()`` call. If the file exists, it is
   snapshotted. The result is ``None`` on success or an
   exception string on failure (which the tool ignores — a
   failed snapshot must NOT block the actual write, otherwise
   the user is stuck).

3. ``list_snapshots()`` returns metadata for the UI to show
   in the Workbench (and to support a future "auto-restore
   to snapshot N" feature).

4. ``prune(max_keep=20)`` removes the oldest snapshots so
   the dir doesn't grow forever.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Hidden subdir under .kairos/. We pick a different name than
# the manual ``workbench/`` so the two don't collide when both
# run.
_AUTO_DIR = "autocheckpoints"
_MAX_KEEP_DEFAULT = 20


@dataclass
class AutoSnapshot:
    """One auto-checkpoint entry (a single file at a point in
    time)."""
    timestamp: str           # e.g. "20260829-140523"
    rel_path: str            # relative to project root
    abs_path: Path           # path to the backup file on disk
    bytes: int               # size of the backup
    mtime: float             # epoch seconds


class AutoCheckpointer:
    """In-process snapshotter for the file-write tools.

    One instance per agent (or per project). Thread-safe by
    convention: the file tools all run inside the same async
    event loop so concurrent writes don't happen in practice,
    but we use ``Path.mkdir(exist_ok=True)`` for the timestamp
    dir so two writes in the same second don't race.
    """

    def __init__(self, project_dir: Path, max_keep: int = _MAX_KEEP_DEFAULT):
        self.project_dir = Path(project_dir).resolve()
        self.max_keep = max_keep
        self._root = self.project_dir / ".kairos" / _AUTO_DIR
        self._root.mkdir(parents=True, exist_ok=True)

    def _ts(self) -> str:
        """Timestamp string for a new snapshot dir."""
        return time.strftime("%Y%m%d-%H%M%S")

    def before_write(self, rel_path: str) -> Optional[str]:
        """Snapshot *rel_path* before an agent write.

        Called by FileEditTool / FileEditReplaceTool /
        MultiEditTool right before the ``write_text()`` call.

        Returns ``None`` on success (or when the file is
        brand-new and there's nothing to back up). Returns
        an error string on failure — but the tool MUST treat
        a failed snapshot as a warning, NOT a hard error:
        a failed snapshot must not block the actual write.
        """
        try:
            # Resolve and validate the path is inside the
            # project (defense in depth — the file tools
            # already resolve safely, but we double-check).
            rel = rel_path.replace("\\", "/").lstrip("/")
            if not rel or rel.startswith("..") or "/../" in ("/" + rel):
                return "path traversal rejected"
            target = (self.project_dir / rel).resolve()
            try:
                target.relative_to(self.project_dir)
            except ValueError:
                return "path outside project root"
            if not target.exists() or not target.is_file():
                return None  # nothing to back up (new file)
            # Use a single timestamp dir per write; if multiple
            # writes happen in the same second, append a
            # counter so they don't overwrite each other.
            ts = self._ts()
            snap_dir = self._root / ts
            counter = 0
            while snap_dir.exists():
                counter += 1
                snap_dir = self._root / f"{ts}-{counter:02d}"
            snap_dir.mkdir(parents=True, exist_ok=True)
            dest = snap_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = target.read_bytes()
            dest.write_bytes(data)
            # Index file so list_snapshots() can find it without
            # walking the tree.
            manifest = snap_dir / ".manifest.json"
            manifest.write_text(json.dumps({
                "timestamp": snap_dir.name,
                "rel_path": rel,
                "bytes": len(data),
                "mtime": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
            self.prune()
            return None
        except Exception as exc:
            # Never block the write — log and return the error
            # so the tool can include it in metadata.
            logger.warning("auto-checkpoint failed for %s: %s",
                           rel_path, exc)
            return str(exc)

    def prune(self, max_keep: Optional[int] = None) -> int:
        """Remove the oldest snapshots so we keep at most
        *max_keep* (default: ``self.max_keep``) on disk.

        Returns the number of dirs removed.
        """
        keep = max_keep if max_keep is not None else self.max_keep
        if not self._root.exists():
            return 0
        # List all timestamp dirs, sorted by name (ISO-like
        # ``YYYYMMDD-HHMMSS`` so lexical sort == chronological).
        all_dirs = sorted([p for p in self._root.iterdir()
                            if p.is_dir()])
        excess = len(all_dirs) - keep
        if excess <= 0:
            return 0
        import shutil
        removed = 0
        for old in all_dirs[:excess]:
            try:
                shutil.rmtree(old)
                removed += 1
            except OSError as exc:
                logger.debug("could not prune %s: %s", old, exc)
        return removed

    def list_snapshots(self) -> List[AutoSnapshot]:
        """Enumerate all auto-snapshots, newest first."""
        out: List[AutoSnapshot] = []
        if not self._root.exists():
            return out
        for d in sorted(self._root.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            manifest = d / ".manifest.json"
            if not manifest.exists():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                out.append(AutoSnapshot(
                    timestamp=data.get("timestamp", d.name),
                    rel_path=data.get("rel_path", ""),
                    abs_path=d / data.get("rel_path", ""),
                    bytes=int(data.get("bytes", 0)),
                    mtime=float(data.get("mtime", 0.0)),
                ))
            except (json.JSONDecodeError, OSError, ValueError):
                continue
        return out
