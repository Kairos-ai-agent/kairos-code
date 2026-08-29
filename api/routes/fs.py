"""Filesystem browse endpoints — power the "click to pick a folder" UX.

R38.6 §25: previously the FolderPicker modal only had a manual path
Input. The user reported "不是填写文档路径，而是直接点击选择本机
文件夹" — they want a click-first experience, not a typing one.

Browser security forbids `window.showDirectoryPicker` from giving
back the absolute path, so the only real way to deliver "click a
folder" is to ask the *backend* (which has full FS access) to list
the directory contents. The frontend renders the result as a
clickable list; clicking a folder navigates into it, double-clicking
(or pressing "Select this folder") commits the path to the project.

Endpoints
---------

GET /api/fs/roots
    Returns the "starting points" the user can pick from. On
    Windows, this is home + kairos workspace + every mounted
    drive letter (A: through Z:, enumerated dynamically via
    ``GetLogicalDrives()`` — covers fixed, removable, and
    network drives). On Linux/macOS, this is home + kairos
    workspace + common mount points (/, /mnt, /media,
    /Volumes, /Users, /home) that exist on the current system.
    Each root has ``{name, path, is_dir}``.

GET /api/fs/list?path=...
    Returns the immediate children of *path*. Each child has
    ``{name, path, is_dir, has_children}``. Symlinks and
    inaccessible entries are filtered out. Paths are returned as
    absolute, OS-native strings (e.g. ``C:\\Users\\me\\projects``).

Security
--------

The path parameter is validated to be an existing directory, but
*not* restricted to a specific root — this is a local dev tool
that the user controls. If a future deployment exposes this
endpoint to the network, add a ``KAIROS_FS_ALLOW_ROOTS`` env var
to lock it down.
"""
from __future__ import annotations

import logging
import os
import string
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from kairos.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FsEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    has_children: bool = False  # True if at least one subdir exists


class FsRoot(FsEntry):
    """A starting-point for the browse UI (a "root" the user can
    pick from — typically home, workspace_dir, and drives on
    Windows)."""


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def _windows_drives() -> List[FsRoot]:
    """Return mounted drives on Windows (C:\\, D:\\, …)."""
    roots: List[FsRoot] = []
    if os.name != "nt":
        return roots
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                path = f"{letter}:\\"
                if Path(path).is_dir():
                    roots.append(FsRoot(
                        name=f"{letter}:",
                        path=path,
                        is_dir=True,
                        has_children=True,
                    ))
    except Exception as exc:
        logger.debug("windows drive enumeration failed: %s", exc)
    return roots


def _home_roots() -> List[FsRoot]:
    """Return home + kairos workspace as roots."""
    roots: List[FsRoot] = []
    home = Path.home()
    if home.is_dir():
        roots.append(FsRoot(
            name=f"~ ({home.name or str(home)})",
            path=str(home),
            is_dir=True,
            has_children=True,
        ))
    ws = settings.workspace_dir
    try:
        # Resolve relative paths against CWD so the displayed path
        # is always absolute (frontend renders it as a pill label
        # and uses it for navigation).
        ws_abs = ws if ws.is_absolute() else (Path.cwd() / ws).resolve()
        if ws_abs.is_dir():
            roots.append(FsRoot(
                name=f"workspace ({ws_abs.name or str(ws_abs)})",
                path=str(ws_abs),
                is_dir=True,
                has_children=True,
            ))
    except OSError:
        pass
    return roots


def _posix_mount_roots() -> List[FsRoot]:
    """Return common mount points on POSIX systems.

    R38.6 §25.2: the user asked "是拉取电脑中的所有盘符吗？否则
    其他人用又是找不到其他盘符" — yes, on Windows we use
    GetLogicalDrives() to enumerate ALL mounted drive letters.
    On Linux/macOS, drive letters don't exist; users have mount
    points instead. We list the common ones that exist on the
    current system so a Linux user with /mnt/data or a Mac user
    with /Volumes/External can browse them just like a Windows
    user can browse E:.

    We only include directories that ACTUALLY EXIST on the
    current system (Path.is_dir check), so the user never sees
    a "phantom" root that 404s when clicked.
    """
    roots: List[FsRoot] = []
    # Common mount points across distros / macOS.
    candidates = [
        ("/",  "FS root (/)"),
        ("/mnt",   "mnt"),
        ("/media", "media"),
        ("/Volumes", "Volumes (macOS)"),
        ("/Users", "Users (macOS)"),
        ("/home",  "home"),
    ]
    for path, label in candidates:
        try:
            p = Path(path)
            if p.is_dir():
                roots.append(FsRoot(
                    name=label,
                    path=str(p),
                    is_dir=True,
                    has_children=True,
                ))
        except (PermissionError, OSError):
            continue
    return roots


@router.get("/fs/roots", response_model=List[FsRoot])
async def list_roots() -> List[FsRoot]:
    """List the user's starting points for the folder picker.

    Returns:
      - the user's home directory (``~``)
      - the kairos ``workspace_dir`` (resolved to absolute path)
      - on Windows: every mounted drive letter (A: through Z:,
        via ``GetLogicalDrives()`` — includes fixed, removable,
        and network drives)
      - on Linux/macOS: common mount points (/, /mnt, /media,
        /Volumes, /Users, /home) that exist on the current system

    The user asked "是拉取电脑中的所有盘符吗？" — yes, this is
    dynamic. It scans the current machine for whatever's mounted
    right now. If a user plugs in a USB drive, restarts the
    backend, then opens the picker, the new drive letter will
    appear in the roots row automatically.
    """
    if os.name == "nt":
        roots = _home_roots() + _windows_drives()
    else:
        roots = _home_roots() + _posix_mount_roots()
    # De-dupe by absolute path (in case workspace_dir == home, or
    # /home is also a separate root).
    seen: set = set()
    out: List[FsRoot] = []
    for r in roots:
        key = os.path.normcase(os.path.abspath(r.path))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/fs/list", response_model=List[FsEntry])
async def list_directory(
    path: str = Query(..., description="Absolute path to list"),
) -> List[FsEntry]:
    """List the immediate children of *path*.

    Returns only directories (not files) so the picker is a "where
    do I want my project to live" tree, not a file browser. Each
    entry has ``has_children`` so the frontend can render a
    disclosure arrow without an extra round-trip.

    Errors:
      - 400: path is empty / not absolute
      - 404: path does not exist or is not a directory
    """
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="path is required")
    p = Path(os.path.abspath(path))
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"path does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {p}")
    try:
        entries: List[FsEntry] = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            # Skip symlinks to avoid loops.
            if child.is_symlink():
                continue
            try:
                if not child.is_dir():
                    continue
                # Probe for children cheaply (one stat call).
                has_children = any(
                    not sub.is_symlink() and sub.is_dir()
                    for sub in child.iterdir()
                )
            except (PermissionError, OSError):
                # Unreadable dir; surface it as a leaf (no children).
                has_children = False
            entries.append(FsEntry(
                name=child.name,
                path=str(child),
                is_dir=True,
                has_children=has_children,
            ))
        return entries
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"permission denied: {exc}")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"list failed: {exc}")
