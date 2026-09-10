"""Workbench API — the backend for the right-side Workbench panel.

R38.6 §26: the right-side panel in minimax-code style requires
4 tabs (File Explorer, Changes, Tasks, Deliverables). This
module provides the HTTP API that powers them, plus a
checkpoint/restore mechanism so the user can undo an
agent's edits without losing other work.

Endpoints
---------

GET /api/workbench/tree?project_id=...&path=...&depth=2
    Recursive file tree starting at *path* (defaults to
    project's work_dir). Each entry has
    ``{name, path, is_dir, has_children, size, mtime, status}``
    where ``status`` is "added" / "modified" / "deleted" / None
    — derived from the project's checkpoint manifest.

GET /api/workbench/file?project_id=...&path=...
    Read a single file's content. Returns ``{content, size,
    is_binary, mtime, status}``. Binary files return a
    placeholder string for ``content``.

GET /api/workbench/diff?project_id=...&path=...
    Unified diff between the checkpoint (pre-edit) version
    and the current version. ``status`` is one of
    "added" / "modified" / "deleted" / "unchanged". Uses
    Python's ``difflib.unified_diff`` so we don't depend on
    git. If the file is binary or huge (> 1MB), the diff
    is a placeholder message.

POST /api/workbench/checkpoint?project_id=...&paths=[...]
    Snapshot the current state of the listed paths (relative
    to work_dir) to the project's checkpoint dir. Future
    restore or diff calls read from this snapshot. If no
    paths are given, snapshots EVERYTHING in work_dir.
    Returns ``{snapshot_id, path_count, size_bytes}``.

POST /api/workbench/restore?project_id=...&path=...
    Restore a single path (or "all") from the latest
    checkpoint. Returns ``{restored, path}``.

GET /api/workbench/tasks?project_id=...&session_id=...
    The task checklist for the project — always non-empty once a
    task exists. Preference order: the Coder's plan todos (with
    their own per-item status), else one item per loop round,
    else the requirement itself. Rounds survive a backend restart
    because they are replayed from the messages table.
    ``{tasks: [{id, title, status, detail, round, source}],
    round, score, last_approve, running, source, task_title}``.

GET /api/workbench/deliverables?project_id=...
    Files the agent has created or modified in the current
    loop. Returns ``{deliverables: [{path, status, ...}]}``.

GET /api/workbench/activity?project_id=...&limit=50
    Recent file change events for the right panel's
    "Files just changed" list. The Coder publishes
    these via message_bus when it writes/reads files.

Storage
-------

Checkpoints are kept at ``<work_dir>/.kairos/workbench/``:
  - ``snapshots/<timestamp>/<path>`` — file contents
  - ``manifest.json`` — {path: {mtime, size, status, hash}}
This stays in the user's project folder, so a backup of
the project (rsync, git) carries the checkpoint state.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api import deps
from kairos.core.message_bus import Message

logger = logging.getLogger(__name__)

router = APIRouter()


def _orch():
    """Resolve the live orchestrator via the deps module.

    Mirrors the shim in projects.py so tests that monkeypatch
    ``api.deps.orchestrator`` are seen by every handler.
    """
    return deps.orchestrator

# Skip these dirs when walking the tree (node_modules, .git, build
# artifacts, etc.). Each entry is matched against the directory's
# basename (not its path) so paths like "node_modules/foo" are
# also skipped. Keep this list short — anything in it is a
# promise that the user never wants to see in the file tree.
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", "dist", "build",
    ".next", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
    "target", ".cargo", ".idea", ".vscode", ".DS_Store",
})

# Files larger than this are treated as "binary" by the file /
# diff endpoints (we don't try to read or diff them).
_MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_project_root(project_id: str) -> Path:
    """Return the project's effective root dir (work_dir or workspace).

    Ensures the root exists (mkdir) so a freshly-created project whose
    folder briefly can't be resolved never 404s the workbench tree —
    the tree then simply shows the (possibly empty) root.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
    root = getattr(project, "work_dir", None) or str(project.workspace)
    p = Path(root).resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only / locked — the tree endpoint reports it
    return p


def _safe_join(root: Path, rel: str) -> Path:
    """Join *rel* to *root* and ensure the result stays inside
    *root* (no path traversal)."""
    if not rel:
        return root
    # Normalize separators and reject absolute paths.
    rel = rel.replace("\\", "/").lstrip("/")
    if rel.startswith("..") or "/../" in ("/" + rel):
        raise HTTPException(status_code=400, detail="path traversal not allowed")
    target = (root / rel).resolve()
    # Defense in depth: check the resolved path is still under root.
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path traversal not allowed")
    return target


def _is_binary(path: Path) -> bool:
    """Return True if *path* looks like a binary file (either by
    extension or by sampling the first 8KB for null bytes)."""
    mt, _ = mimetypes.guess_type(str(path))
    if mt and not mt.startswith("text/") and mt != "application/json":
        # Known image / binary MIME type.
        if not mt.startswith("application/"):
            return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return True


def _checkpoint_dir(root: Path) -> Path:
    d = root / ".kairos" / "workbench"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(root: Path) -> Path:
    return _checkpoint_dir(root) / "manifest.json"


def _load_manifest(root: Path) -> dict:
    p = _manifest_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(root: Path, manifest: dict) -> None:
    p = _manifest_path(root)
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                 encoding="utf-8")


def _hash_file(path: Path) -> str:
    """Quick file hash for change detection. Reads up to 1MB."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(1 * 1024 * 1024))
    except OSError:
        return ""
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FileEntry(BaseModel):
    name: str
    path: str  # relative to work_dir
    is_dir: bool
    has_children: bool = False
    size: int = 0
    mtime: float = 0.0
    status: Optional[str] = None  # "added" | "modified" | "deleted" | None
    skip_reason: Optional[str] = None  # "binary" | "too_large" | "ignored"


class TreeResponse(BaseModel):
    root: str
    entries: List[FileEntry]


class FileContent(BaseModel):
    path: str
    content: str
    size: int
    is_binary: bool = False
    mtime: float = 0.0
    status: Optional[str] = None
    checkpoint_path: Optional[str] = None  # if a snapshot exists


class FileDiff(BaseModel):
    path: str
    status: str  # "added" | "modified" | "deleted" | "unchanged"
    diff: str
    current_size: int
    checkpoint_size: int


class CheckpointResult(BaseModel):
    snapshot_id: str
    path_count: int
    size_bytes: int


class RestoreResult(BaseModel):
    restored: int
    path: str


class TaskItem(BaseModel):
    id: str = ""
    title: str
    status: str  # "pending" | "in_progress" | "done" | "rejected" | "failed"
    detail: Optional[str] = None
    round: Optional[int] = None
    session_id: str = ""
    source: str = ""  # "plan" | "round" | "task"
    timestamp: float = 0.0


class TasksResponse(BaseModel):
    tasks: List[TaskItem]
    round: int = 0
    score: int = 0
    last_approve: bool = False
    running: bool = False
    # R38.6.6: the tracker must always have something to show, even for
    # a task the Coder never decomposed into a plan. ``source`` says
    # which data the list came from and ``task_title`` carries the
    # requirement so the panel can render a header.
    source: str = "none"  # "plan" | "rounds" | "task" | "none"
    task_title: str = ""
    session_id: str = ""


class DeliverableItem(BaseModel):
    path: str
    name: str
    status: str  # "added" | "modified"
    size: int = 0
    mtime: float = 0.0
    description: Optional[str] = None


class DeliverablesResponse(BaseModel):
    deliverables: List[DeliverableItem]


class ActivityItem(BaseModel):
    timestamp: float
    sender: str
    path: str
    action: str  # "read" | "write" | "edit" | "delete" | "create"
    size: Optional[int] = None


class ActivityResponse(BaseModel):
    activity: List[ActivityItem]


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------


def _walk(root: Path, current: Path, rel: str, depth: int,
          manifest: dict) -> List[FileEntry]:
    """Recursively list *current* (under *root*) up to *depth*."""
    out: List[FileEntry] = []
    try:
        items = sorted(current.iterdir(),
                       key=lambda p: (not p.is_dir(), p.name.lower()))
    except (PermissionError, OSError):
        return out
    for child in items:
        if child.name in _SKIP_DIRS:
            continue
        if child.name.startswith(".") and child.name != ".env":
            # Skip dotfiles (.env.example, .gitignore, etc are visible
            # but .npmrc, .cache, .config — too noisy). We *do* show
            # .env so the user can verify their env config.
            if child.name not in (".env", ".env.example", ".gitignore",
                                   ".gitkeep", ".dockerignore",
                                   ".eslintrc", ".prettierrc"):
                continue
        try:
            st = child.stat()
        except OSError:
            continue
        rel_child = (Path(rel) / child.name).as_posix() if rel else child.name
        is_dir = child.is_dir()
        # Determine status from manifest. Manifest stores either
        # "added" (was not there at snapshot time) or
        # "modified" (hash differs). "deleted" is computed by
        # walking and seeing the manifest has a key but the
        # file is gone — that's done in the caller.
        rec = manifest.get(rel_child)
        status: Optional[str] = None
        if rec is not None:
            if is_dir:
                pass  # directories don't get a status
            else:
                cur_hash = _hash_file(child)
                if cur_hash != rec.get("hash", ""):
                    status = "modified"
        entry = FileEntry(
            name=child.name,
            path=rel_child,
            is_dir=is_dir,
            has_children=False,  # filled below
            size=st.st_size if not is_dir else 0,
            mtime=st.st_mtime,
            status=status,
        )
        if is_dir and depth > 0:
            entry.has_children = any(
                not (p.name in _SKIP_DIRS) and not p.name.startswith(".")
                for p in child.iterdir()
            ) if child.exists() else False
        out.append(entry)
        if is_dir and depth > 0:
            # Recurse one level deep so the tree can be expanded lazily.
            out.extend(_walk(root, child, rel_child, depth - 1, manifest))
    return out


@router.post("/workbench/open-folder")
async def open_folder(
    project_id: str = Query(..., description="Project id"),
) -> dict:
    """Open the project work_dir in the OS file explorer.

    R38.6.4: convenience button next to the Files tab. Spawns
    explorer.exe (Windows) / xdg-open (Linux) / open (macOS) as
    a detached subprocess so the response returns immediately.
    Chrome blocks file:// from JS for security, so we proxy through
    the backend.

    R38.6.4.2: if the resolved path doesn't exist, we now look at
    a few alternates before giving up. Projects created earlier
    may store a relative ``work_dir`` like ``./workspace/...`` that
    resolves to different absolute paths depending on the backend
    process's CWD at request time. Common fallbacks we try:
      1. the resolved path (preferred)
      2. the raw stored ``work_dir`` (sometimes already absolute)
      3. ``./workspace/<project_id>`` (matches New chat creation)
      4. the orchestrator's ``workspace_base`` default
    The first one that exists wins. We also report every candidate
    in the response so the user can see what the backend saw.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(
            status_code=404, detail=f"project not found: {project_id}")
    raw_wd = getattr(project, "work_dir", None) or str(
        getattr(project, "workspace", ""))

    candidates: list[Path] = []
    try:
        candidates.append(Path(raw_wd).resolve())
    except Exception:
        pass
    if raw_wd:
        candidates.append(Path(raw_wd))
    # The orchestrator's per-project workspace — it is mkdir'd every
    # time the project loads, so it is the reliable fallback when
    # work_dir points at a folder that no longer exists on this
    # machine (a path from another computer / unmounted drive).
    try:
        ws_attr = getattr(project, "workspace", None)
        if ws_attr:
            candidates.append(Path(str(ws_attr)).resolve())
    except Exception:
        pass
    # Common default location New chat uses — anchored to the repo's
    # workspace dir (NOT CWD-relative: the backend can be launched
    # from any directory, and a CWD-relative "./workspace" candidate
    # is exactly why open-folder kept returning "not found").
    try:
        from kairos.config.settings import settings
        candidates.append(settings.workspace_dir / project_id)
    except Exception:
        candidates.append(Path("./workspace") / project_id)
    # Orchestrator default
    try:
        candidates.append(Path(getattr(_orch(), "workspace_base", "./workspace")))
    except Exception:
        pass

    seen: set[Path] = set()
    root: Path | None = None
    for c in candidates:
        try:
            c = c.resolve()
        except Exception:
            continue
        if c in seen:
            continue
        seen.add(c)
        if c.exists():
            root = c
            break
    if root is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "no work_dir candidate exists. Tried: "
                + ", ".join(str(c) for c in candidates)))

    # Make sure the path exists before launching the file manager —
    # some platforms (e.g. xdg-open) refuse to open non-existent
    # paths. We try a best-effort mkdir; if it fails (read-only,
    # permission denied, etc.) we still return the resolved path so
    # the user can see what the backend saw.
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning(
            "open-folder: mkdir failed for %s: %s", root, exc)

    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(root)],
                             creationflags=getattr(
                                 subprocess, "DETACHED_PROCESS", 0))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(root)], start_new_session=True)
        else:
            subprocess.Popen(["xdg-open", str(root)], start_new_session=True)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"no file manager available: {exc}")
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"failed to open folder: {type(exc).__name__}: {exc}")
    return {
        "ok": True,
        "path": str(root),
        "candidates_tried": [str(c) for c in candidates],
    }


@router.get("/workbench/tree", response_model=TreeResponse)
async def get_tree(
    project_id: str = Query(..., description="Project id"),
    path: str = Query("", description="Relative path under work_dir; '' for root"),
    depth: int = Query(2, description="How many levels to recurse (0..4)"),
) -> TreeResponse:
    root = _resolve_project_root(project_id)
    current = _safe_join(root, path)
    if not current.exists():
        raise HTTPException(status_code=404, detail=f"path does not exist: {path}")
    manifest = _load_manifest(root)
    entries = _walk(root, current, path, max(0, min(depth, 4)), manifest)
    return TreeResponse(root=str(root), entries=entries)


# ---------------------------------------------------------------------------
# File content
# ---------------------------------------------------------------------------


@router.get("/workbench/file", response_model=FileContent)
async def get_file(
    project_id: str = Query(...),
    path: str = Query(..., description="Relative path under work_dir"),
) -> FileContent:
    root = _resolve_project_root(project_id)
    target = _safe_join(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="path is a directory")
    st = target.stat()
    manifest = _load_manifest(root)
    rec = manifest.get(path)
    is_binary = _is_binary(target)
    if is_binary or st.st_size > _MAX_FILE_BYTES:
        return FileContent(
            path=path,
            content=f"[binary file, {st.st_size} bytes — preview disabled]",
            size=st.st_size,
            is_binary=True,
            mtime=st.st_mtime,
            status="modified" if rec and rec.get("hash", "")
                                       != _hash_file(target) else None,
            checkpoint_path=str(
                _checkpoint_dir(root) / rec["snapshot_path"]) if rec else None,
        )
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"read failed: {exc}")
    return FileContent(
        path=path,
        content=content,
        size=st.st_size,
        is_binary=False,
        mtime=st.st_mtime,
        status="modified" if rec and rec.get("hash", "")
                                       != _hash_file(target) else None,
        checkpoint_path=str(
            _checkpoint_dir(root) / rec["snapshot_path"]) if rec else None,
    )


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


@router.get("/workbench/diff", response_model=FileDiff)
async def get_diff(
    project_id: str = Query(...),
    path: str = Query(...),
) -> FileDiff:
    root = _resolve_project_root(project_id)
    target = _safe_join(root, path)
    manifest = _load_manifest(root)
    rec = manifest.get(path)
    if not rec:
        # No snapshot — file is either unchanged or never tracked.
        return FileDiff(
            path=path, status="unchanged", diff="",
            current_size=target.stat().st_size if target.exists() else 0,
            checkpoint_size=0,
        )
    snap_path = _checkpoint_dir(root) / rec["snapshot_path"]
    current_text = ""
    current_size = 0
    if target.exists() and not _is_binary(target):
        try:
            current_text = target.read_text(encoding="utf-8", errors="replace")
            current_size = target.stat().st_size
        except OSError:
            pass
    checkpoint_text = ""
    checkpoint_size = 0
    if snap_path.exists():
        try:
            checkpoint_text = snap_path.read_text(encoding="utf-8", errors="replace")
            checkpoint_size = snap_path.stat().st_size
        except OSError:
            pass
    if not target.exists():
        status = "deleted"
    elif not rec.get("existed", True):
        status = "added"
    elif rec.get("hash", "") != _hash_file(target):
        status = "modified"
    else:
        status = "unchanged"
    if status == "unchanged":
        return FileDiff(path=path, status=status, diff="",
                        current_size=current_size, checkpoint_size=checkpoint_size)
    diff_lines = list(difflib.unified_diff(
        checkpoint_text.splitlines(keepends=True),
        current_text.splitlines(keepends=True),
        fromfile=f"checkpoint/{path}", tofile=path, n=3,
    ))
    if _is_binary(target) or current_size > _MAX_FILE_BYTES:
        diff_text = f"[binary or large file, {current_size} bytes — diff disabled]"
    else:
        diff_text = "".join(diff_lines) if diff_lines else "[no textual diff]"
    return FileDiff(
        path=path, status=status, diff=diff_text,
        current_size=current_size, checkpoint_size=checkpoint_size,
    )


# ---------------------------------------------------------------------------
# Checkpoint / Restore
# ---------------------------------------------------------------------------


class CheckpointRequest(BaseModel):
    paths: Optional[List[str]] = None  # None = all files


@router.post("/workbench/checkpoint", response_model=CheckpointResult)
async def checkpoint(
    project_id: str = Query(...),
    body: CheckpointRequest = CheckpointRequest(),
) -> CheckpointResult:
    root = _resolve_project_root(project_id)
    snap_id = time.strftime("%Y%m%d-%H%M%S")
    snap_dir = _checkpoint_dir(root) / "snapshots" / snap_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(root)
    # Clear previous manifest so the new one reflects only the
    # current snapshot. (Otherwise old "added" statuses leak in.)
    manifest = {}
    paths = body.paths
    count = 0
    size_bytes = 0
    if paths is None:
        # Snapshot every text file under root.
        targets: List[Path] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(seg in _SKIP_DIRS for seg in rel.split("/")):
                continue
            targets.append(p)
    else:
        targets = [_safe_join(root, p) for p in paths]
    for p in targets:
        if not p.exists() or not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        try:
            data = p.read_bytes()
        except OSError:
            continue
        # Skip binary/huge files in the snapshot to keep it small.
        if len(data) > _MAX_FILE_BYTES or b"\x00" in data[:8192]:
            continue
        snap_file = snap_dir / rel
        snap_file.parent.mkdir(parents=True, exist_ok=True)
        snap_file.write_bytes(data)
        manifest[rel] = {
            "snapshot_path": str(Path("snapshots") / snap_id / rel),
            "hash": _hash_file(p),
            "mtime": p.stat().st_mtime,
            "size": len(data),
            "existed": True,
        }
        count += 1
        size_bytes += len(data)
    _save_manifest(root, manifest)
    # Broadcast a "checkpoint created" event so the panel
    # refreshes (the file statuses will update).
    bus = _orch().message_bus
    await bus.publish(Message(
        sender="workbench", receiver="user", topic="workbench.checkpoint",
        content=json.dumps({"snapshot_id": snap_id, "path_count": count}),
        msg_type="text",
        metadata={"project_id": project_id, "snapshot_id": snap_id},
    ))
    return CheckpointResult(snapshot_id=snap_id, path_count=count,
                            size_bytes=size_bytes)


class RestoreRequest(BaseModel):
    path: str  # "all" or a relative path


@router.post("/workbench/restore", response_model=RestoreResult)
async def restore(
    project_id: str = Query(...),
    body: RestoreRequest = RestoreRequest(path="all"),
) -> RestoreResult:
    root = _resolve_project_root(project_id)
    manifest = _load_manifest(root)
    if body.path == "all":
        targets = list(manifest.keys())
    else:
        if body.path not in manifest:
            raise HTTPException(
                status_code=404,
                detail=f"no checkpoint for path: {body.path}",
            )
        targets = [body.path]
    restored = 0
    for rel in targets:
        rec = manifest[rel]
        snap_file = _checkpoint_dir(root) / rec["snapshot_path"]
        if not snap_file.exists():
            continue
        target = _safe_join(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(snap_file.read_bytes())
        restored += 1
    # Broadcast restore event.
    bus = _orch().message_bus
    await bus.publish(Message(
        sender="workbench", receiver="user", topic="workbench.restore",
        content=json.dumps({"restored": restored, "path": body.path}),
        msg_type="text",
        metadata={"project_id": project_id, "path": body.path,
                  "restored_count": restored},
    ))
    return RestoreResult(restored=restored, path=body.path)


# ---------------------------------------------------------------------------
# Tasks (the "✓ progress" list)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task tracker (R38.6.6)
#
# The tracker has to answer three questions for a long task:
#   1. what are the work items?            → plan todos, else loop rounds
#   2. which of them are done?             → per-item status, never a guess
#   3. what if the Coder never made a plan? → still show the task
#
# The previous implementation read ``session.history[-1]["plan"]`` and
# walked it as a list of step dicts. A plan snapshot is actually
# ``{"todos": [{"status", "content", "activeForm"}], "updated_at": …}``
# — a DICT — so ``enumerate()`` yielded the key strings, every entry
# failed ``isinstance(step, dict)`` and the task list came back empty.
# Hence "No tasks yet" for every long task. And after a backend restart
# even that was unavailable: the loop session (and its plan) only ever
# lives in memory.
# ---------------------------------------------------------------------------

#: Loop events that describe the work units of a long task. Replayed
#: oldest-first from the messages table when no live session exists.
_TASK_EVENT_TOPICS = (
    "loop.started", "loop.coder_started", "loop.reviewer_started",
    "task.result", "task.error", "agent.response", "plan.updated",
)

#: The Coder's plan vocabulary → the tracker's.
_TODO_STATUS = {
    "completed": "done", "complete": "done", "done": "done",
    "in_progress": "in_progress", "running": "in_progress",
    "active": "in_progress",
    "pending": "pending", "todo": "pending", "open": "pending",
    "rejected": "rejected",
    "failed": "failed", "error": "failed",
    "cancelled": "failed", "canceled": "failed", "skipped": "failed",
}


def _meta_of(ev: dict) -> dict:
    """Metadata of a message row (JSON string in the DB, dict in memory)."""
    md = ev.get("metadata")
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except Exception:
            return {}
    return md if isinstance(md, dict) else {}


def _first_line(text: Any, limit: int = 70) -> str:
    """One tidy line of prose — used for round titles."""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    if not text or text in ("null", "None", "{}", "[]"):
        return ""
    raw = text.strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    line = re.sub(r'^[#*\->\s]+', '', line)
    line = re.sub(r'\s+', ' ', line)
    return line[:limit]


def _todos_of(plan: Any) -> List[dict]:
    """Normalise a plan snapshot into a list of todo dicts.

    Accepts a ``Plan`` object, the ``{"todos": [...]}`` dict stored in
    ``session.history`` / ``plan.updated``, or a bare list. Anything
    else yields ``[]``.
    """
    if plan is None:
        return []
    if hasattr(plan, "to_dict"):
        try:
            plan = plan.to_dict()
        except Exception:
            return []
    if isinstance(plan, dict):
        plan = plan.get("todos") or plan.get("items") or plan.get("steps") or []
    if not isinstance(plan, (list, tuple)):
        return []
    return [t for t in plan if isinstance(t, dict)]


def _todo_title(todo: dict, idx: int) -> str:
    for key in ("content", "title", "task", "description", "activeForm"):
        val = todo.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return f"Step {idx + 1}"


def _todo_detail(todo: dict) -> Optional[str]:
    for key in ("activeForm", "description", "detail"):
        val = todo.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _verdict_from_content(content: Any) -> Optional[dict]:
    """Parse a Reviewer verdict out of a ``task.result`` payload.

    Stored content is capped at 2000 chars, so long verdicts are cut
    mid-string and ``json.loads`` fails. The Reviewer prompt always
    emits ``approve`` / ``score`` first, so a regex over the leading
    fields recovers what the tracker needs.
    """
    text = content if isinstance(content, str) else json.dumps(content or {}, ensure_ascii=False)
    data: Any = None
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except Exception:
            data = None
    if isinstance(data, dict) and "approve" in data:
        return data
    m = re.search(r'"approve"\s*:\s*(true|false)', text, re.I)
    if not m:
        return None
    out: Dict[str, Any] = {"approve": m.group(1).lower() == "true"}
    m_score = re.search(r'"score"\s*:\s*(-?\d+)', text)
    if m_score:
        out["score"] = int(m_score.group(1))
    m_sum = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.){0,200})', text)
    if m_sum:
        out["summary"] = m_sum.group(1)
    return out


def _verdict_detail(verdict: dict) -> str:
    """One-line human summary of a verdict: score, outcome, top issue."""
    bits = [f"score {int(verdict.get('score') or 0)}",
            "approved" if verdict.get("approve") else "rejected"]
    issues = verdict.get("issues") or []
    if isinstance(issues, list) and issues and isinstance(issues[0], dict):
        desc = str(issues[0].get("description") or "").strip()
        if desc:
            bits.append(f"{issues[0].get('severity', '?')}: {desc[:80]}")
    summary = _first_line(verdict.get("summary"), 100)
    if summary:
        bits.append(summary)
    return " · ".join(bits)


def _task_title_of(project: Any, session: Any) -> str:
    """The requirement this tracker is tracking, as one short line."""
    for src in (getattr(session, "original_requirement", "") if session else "",
                getattr(project, "requirements", "") if project else ""):
        line = _first_line(src, 90)
        if line:
            return line
    return ""


def _with_note(detail: Optional[str], note: str) -> str:
    """Keep the item's own context and append why it stopped."""
    return f"{detail} · {note}" if detail else note


def _plan_task_items(session: Any, round_n: int, running: bool,
                     last_approve: bool) -> List[TaskItem]:
    """Merge every plan snapshot the session kept — last write wins.

    Each snapshot is keyed by todo content, so a todo that was
    ``in_progress`` in round 3 and ``completed`` in round 5 shows as
    done, and items added later appear at the end.
    """
    snapshots: List[tuple] = []
    for item in list(getattr(session, "history", []) or []):
        if isinstance(item, dict):
            todos = _todos_of(item.get("plan"))
            if todos:
                snapshots.append((int(item.get("round") or 0), todos))
    live = _todos_of(getattr(session, "plan_todos", None))
    if live:
        snapshots.append((round_n, live))
    merged: "Dict[str, dict]" = {}
    order: List[str] = []
    for rnd, todos in snapshots:
        for idx, todo in enumerate(todos):
            title = _todo_title(todo, idx)
            status = _TODO_STATUS.get(str(todo.get("status") or "").lower(), "pending")
            detail = _todo_detail(todo)
            if (not running) and (not last_approve) and status == "in_progress":
                # the loop ended without approving: this item never finished
                status = "failed"
                detail = _with_note(detail, "loop ended before this item finished")
            key = title
            if key not in merged:
                order.append(key)
            merged[key] = {"title": title, "status": status,
                           "detail": detail,
                           "round": rnd or round_n}
    sid = str(getattr(session, "session_id", "") or "")
    out: List[TaskItem] = []
    for i, key in enumerate(order):
        info = merged[key]
        out.append(TaskItem(
            id=f"plan-{i + 1}", title=info["title"], status=info["status"],
            detail=info["detail"], round=info["round"], session_id=sid,
            source="plan",
        ))
    return out


def _round_items_from_session(session: Any, running: bool) -> List[TaskItem]:
    """Work items for a task with no plan: one per completed round."""
    sid = str(getattr(session, "session_id", "") or "")
    current = int(getattr(session, "round", 0) or 0)
    seen: Dict[int, TaskItem] = {}
    for item in list(getattr(session, "history", []) or []):
        if not isinstance(item, dict):
            continue
        rnd = int(item.get("round") or 0)
        if rnd <= 0:
            continue
        review = item.get("review") if isinstance(item.get("review"), dict) else None
        if item.get("rollback"):
            status = "failed"
            detail = f"rolled back: {item.get('reason') or 'score regression'}"
        elif review is not None:
            status = "done" if review.get("approve") else "rejected"
            detail = _verdict_detail(review)
        else:
            status, detail = "pending", None
        title = (_first_line(review.get("summary"), 70) if review else "") or f"Round {rnd}"
        seen[rnd] = TaskItem(
            id=f"round-{rnd}", title=title, status=status, detail=detail,
            round=rnd, session_id=sid, source="round",
        )
    if current and running and current not in seen and seen:
        # Only synthesise the in-flight round once at least one round
        # finished; before that the endpoint shows the requirement
        # itself, which is far more useful than "Round 1".
        seen[current] = TaskItem(
            id=f"round-{current}", title=f"Round {current}", status="in_progress",
            detail="running…", round=current, session_id=sid, source="round",
        )
    return [seen[k] for k in sorted(seen)]


def _plan_items_from_events(events: List[dict], running: bool,
                            session_id: str = "") -> List[TaskItem]:
    """Rebuild the Coder's plan from persisted ``plan.updated`` events.

    A decomposed plan is the best possible checklist, so it is
    preferred over rounds even after a restart. The newest snapshot
    wins; the Coder's own per-item status is preserved.
    """
    newest: List[dict] = []
    for ev in events:
        if ev.get("topic") != "plan.updated":
            continue
        todos = _todos_of(_meta_of(ev).get("plan"))
        if todos:
            newest = todos
    if not newest:
        return []
    items: List[TaskItem] = []
    for idx, todo in enumerate(newest):
        title = _todo_title(todo, idx)
        status = _TODO_STATUS.get(str(todo.get("status") or "").lower(), "pending")
        detail = _todo_detail(todo)
        if not running and status == "in_progress":
            status = "failed"
            detail = _with_note(detail, "loop stopped before this item finished")
        items.append(TaskItem(
            id=f"plan-{idx + 1}", title=title, status=status,
            detail=detail, session_id=session_id, source="plan",
        ))
    return items


def _round_items_from_events(events: List[dict],
                             running: bool) -> tuple:
    """Rebuild the round list from persisted loop events.

    Used when the in-memory session is gone (backend restart): the
    messages table still holds ``loop.coder_started`` (one per round,
    ``metadata.round``) and the Reviewer's ``task.result`` verdicts,
    which is enough to render the same checklist.
    """
    latest_sid = ""
    for ev in events:
        sid = str(_meta_of(ev).get("session_id") or "")
        if sid:
            latest_sid = sid
    starts: Dict[int, float] = {}
    for ev in events:
        if ev.get("topic") != "loop.coder_started":
            continue
        md = _meta_of(ev)
        sid = str(md.get("session_id") or "")
        if latest_sid and sid and sid != latest_sid:
            continue
        rnd = int(md.get("round") or 0)
        if rnd > 0:
            starts.setdefault(rnd, float(ev.get("timestamp") or 0.0))
    if not starts:
        return [], latest_sid

    def _round_at(ts: float) -> int:
        best = 0
        for rnd, started in starts.items():
            if started <= ts and rnd > best:
                best = rnd
        return best

    verdicts: Dict[int, dict] = {}
    errors: Dict[int, str] = {}
    titles: Dict[int, str] = {}
    for ev in events:
        topic = ev.get("topic")
        ts = float(ev.get("timestamp") or 0.0)
        rnd = _round_at(ts)
        if rnd <= 0:
            continue
        sender = str(ev.get("sender") or "")
        if topic == "task.result":
            if "reviewer" in sender:
                verdict = _verdict_from_content(ev.get("content"))
                if verdict:
                    verdicts[rnd] = verdict
        elif topic == "task.error":
            if rnd not in errors:
                errors[rnd] = _first_line(ev.get("content"), 90)
        elif topic == "agent.response" and sender.endswith(".coder"):
            line = _first_line(ev.get("content"), 70)
            if line:
                titles[rnd] = line

    last_round = max(starts)
    out: List[TaskItem] = []
    for rnd in sorted(starts):
        verdict = verdicts.get(rnd)
        if verdict is not None:
            status = "done" if verdict.get("approve") else "rejected"
            detail: Optional[str] = _verdict_detail(verdict)
        elif rnd in errors:
            status, detail = "failed", errors[rnd]
        elif rnd == last_round and running:
            status, detail = "in_progress", "running…"
        elif rnd == last_round:
            status, detail = "failed", "loop ended without a verdict"
        else:
            # The round ran but the Reviewer never returned a verdict
            # (tool-call limit, crash, …) — it did not pass review.
            status, detail = "failed", "round finished without a verdict"
        title = titles.get(rnd) or ""
        summary = _first_line((verdict or {}).get("summary"), 70)
        out.append(TaskItem(
            id=f"round-{rnd}", title=summary or title or f"Round {rnd}",
            status=status, detail=detail, round=rnd, session_id=latest_sid,
            source="round",
        ))
    return out, latest_sid


@router.get("/workbench/tasks", response_model=TasksResponse)
async def get_tasks(
    project_id: str = Query(...),
    session_id: str = Query(""),
) -> TasksResponse:
    """Return the task checklist for a project — always, if a task exists.

    Order of preference:
      1. the live session's plan todos (the Coder's own decomposition,
         with its own per-item status),
      2. the live session's rounds (a task the Coder never decomposed),
      3. the persisted loop events (after a backend restart), and
      4. the bare requirement, so the panel is never blank while a
         task exists.

    Status mapping: the Coder's ``completed`` → ``done``, ``in_progress``
    → ``in_progress``; a round the Reviewer rejected → ``rejected``
    (finished, not accepted); a round that errored → ``failed``.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404,
                            detail=f"project not found: {project_id}")
    session = getattr(project, "loop_session", None)
    if session_id and session is not None:
        if str(getattr(session, "session_id", "") or "") != session_id:
            session = None  # asked for a different (past) session
    running = bool(getattr(project, "loop_task", None)
                   and not project.loop_task.done())
    round_n = int(getattr(session, "round", 0) or 0) if session else 0
    last_score = int(getattr(session, "last_score", 0) or 0) if session else 0
    last_approve = bool(getattr(session, "last_approve", False)) if session else False
    sid = str(getattr(session, "session_id", "") or "") if session else session_id
    task_title = _task_title_of(project, session)

    tasks: List[TaskItem] = []
    source = "none"
    if session is not None:
        tasks = _plan_task_items(session, round_n, running, last_approve)
        if tasks:
            source = "plan"
        else:
            tasks = _round_items_from_session(session, running)
            if tasks:
                source = "rounds"
        if not tasks and task_title:
            # A task exists but has produced no plan and no round yet.
            tasks = [TaskItem(
                id="task-1", title=task_title,
                status="in_progress" if running else "pending",
                round=round_n, session_id=sid, source="task",
            )]
            source = "task"
    if not tasks:
        # No live session (fresh backend): replay the persisted events.
        db = getattr(_orch(), "_db", None)
        events: List[dict] = []
        if db is not None:
            try:
                events = db.load_events(project_id, list(_TASK_EVENT_TOPICS))
            except Exception:
                logger.debug("task tracker: load_events failed", exc_info=True)
        tasks = _plan_items_from_events(events, running, sid)
        if tasks:
            source = "plan"
        else:
            tasks, replayed_sid = _round_items_from_events(events, running)
            if tasks:
                source = "rounds"
                sid = sid or replayed_sid
            elif task_title:
                tasks = [TaskItem(
                    id="task-1", title=task_title,
                    status="in_progress" if running else "pending",
                    session_id=sid, source="task",
                )]
                source = "task"
    return TasksResponse(
        tasks=tasks, round=round_n, score=last_score,
        last_approve=last_approve, running=running, source=source,
        task_title=task_title, session_id=sid,
    )


# ---------------------------------------------------------------------------
# Deliverables
# ---------------------------------------------------------------------------


@router.get("/workbench/deliverables", response_model=DeliverablesResponse)
async def get_deliverables(
    project_id: str = Query(...),
) -> DeliverablesResponse:
    """List files modified or created since the latest checkpoint.

    These are the user's "what did the agent actually do?"
    answer. Empty if no checkpoint exists (then every file is
    a candidate deliverable, which is too noisy to surface).
    """
    root = _resolve_project_root(project_id)
    manifest = _load_manifest(root)
    out: List[DeliverableItem] = []
    for rel, rec in sorted(manifest.items()):
        target = _safe_join(root, rel)
        if not target.exists():
            # Was deleted since checkpoint.
            out.append(DeliverableItem(
                path=rel, name=Path(rel).name, status="deleted",
                size=rec.get("size", 0), mtime=rec.get("mtime", 0.0),
                description="deleted since checkpoint",
            ))
            continue
        cur_hash = _hash_file(target)
        if cur_hash == rec.get("hash", ""):
            status = "unchanged"
        else:
            # Was "added" if the snapshot didn't have it, else
            # "modified".
            status = "added" if not rec.get("existed", True) else "modified"
        try:
            st = target.stat()
        except OSError:
            continue
        out.append(DeliverableItem(
            path=rel, name=target.name, status=status,
            size=st.st_size, mtime=st.st_mtime,
        ))
    return DeliverablesResponse(deliverables=out)


# ---------------------------------------------------------------------------
# Activity (recent file changes streamed from message_bus)
# ---------------------------------------------------------------------------


@router.get("/workbench/activity", response_model=ActivityResponse)
async def get_activity(
    project_id: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
) -> ActivityResponse:
    """Recent file-change events for this project.

    The Coder publishes ``tool.result`` events with file paths
    on every read/write/edit. We surface them so the right
    panel can show a "Files just changed" feed.
    """
    bus = _orch().message_bus
    history = bus.get_history(limit=500, project_id=project_id)
    out: List[ActivityItem] = []
    for msg in history:
        meta = getattr(msg, "metadata", {}) or {}
        if meta.get("project_id") and meta["project_id"] != project_id:
            continue
        topic = getattr(msg, "topic", "") or ""
        if topic not in ("tool.call", "tool.result", "workbench.checkpoint",
                          "workbench.restore", "file.changed"):
            continue
        path = (getattr(msg, "content", None) or "").strip()
        if not path:
            continue
        action = "write" if topic == "tool.call" else "read"
        if topic == "workbench.checkpoint":
            action = "checkpoint"
        elif topic == "workbench.restore":
            action = "restore"
        out.append(ActivityItem(
            timestamp=getattr(msg, "timestamp", 0.0) or 0.0,
            sender=getattr(msg, "sender", ""),
            path=path,
            action=action,
        ))
        if len(out) >= limit:
            break
    return ActivityResponse(activity=out)
