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

GET /api/workbench/tasks?project_id=...
    List the current loop's planned tasks with status
    (pending / in_progress / done). The Coder emits
    these as part of its planning phase. ``{tasks: [...],
    round, score, last_approve}``.

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
import time
from pathlib import Path
from typing import List, Optional

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
    title: str
    status: str  # "pending" | "in_progress" | "done" | "failed"
    detail: Optional[str] = None
    round: Optional[int] = None
    timestamp: float = 0.0


class TasksResponse(BaseModel):
    tasks: List[TaskItem]
    round: int = 0
    score: int = 0
    last_approve: bool = False
    running: bool = False


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


@router.get("/workbench/tasks", response_model=TasksResponse)
async def get_tasks(
    project_id: str = Query(...),
) -> TasksResponse:
    """Return the current loop's tasks with checkmark status.

    The Coder emits ``task.plan`` events as it plans; we read the
    loop_session.history for the plan items + the latest
    round to derive which are done.

    Status mapping:
      - task in plan and round < N: pending
      - task in plan and round == N: in_progress
      - task in plan and round < latest round: done
      - task in plan that failed: failed
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
    session = getattr(project, "loop_session", None)
    tasks: List[TaskItem] = []
    if session is None:
        return TasksResponse(tasks=[], running=False)
    round_n = int(getattr(session, "round", 0) or 0)
    last_score = int(getattr(session, "last_score", 0) or 0)
    last_approve = bool(getattr(session, "last_approve", False))
    running = bool(project.loop_task and not project.loop_task.done())
    # Walk session.history. Each history item may have a "plan"
    # key (a list of step dicts with 'title' / 'description') and
    # an "issues" list. We surface the plan steps as the task
    # list, and the latest round's issues as failures.
    history = list(getattr(session, "history", []) or [])
    if not history:
        return TasksResponse(tasks=[], round=round_n, score=last_score,
                            last_approve=last_approve, running=running)
    latest = history[-1]
    plan = latest.get("plan") or []
    issues = latest.get("issues") or []
    issue_set = set()
    for iss in issues:
        if isinstance(iss, dict):
            title = iss.get("title") or iss.get("description")
            if title:
                issue_set.add(title)
    for i, step in enumerate(plan):
        if not isinstance(step, dict):
            continue
        title = step.get("title") or step.get("description") or f"Step {i+1}"
        # Naive status mapping: if there's only one plan, every
        # step is "in_progress" while running, "done" when loop
        # ended. For multi-round loops the user gets a
        # round-keyed view in the Loop page; this is the
        # single-round overview.
        if not running:
            status = "done"
        else:
            status = "in_progress"
        if title in issue_set:
            status = "failed"
        tasks.append(TaskItem(
            title=title,
            status=status,
            detail=step.get("description") if isinstance(step.get("description"), str) else None,
            round=round_n,
            timestamp=time.time(),
        ))
    return TasksResponse(tasks=tasks, round=round_n, score=last_score,
                          last_approve=last_approve, running=running)


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
