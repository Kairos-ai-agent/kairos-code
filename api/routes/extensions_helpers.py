"""Helpers extracted from api.routes.extensions (route registration unchanged)."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


def _parse_frontmatter(md: str) -> dict:
    """Extract YAML frontmatter from a SKILL.md. Returns
    {name, description} as a dict. If no frontmatter, returns {}.
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", md, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    out: dict = {}
    for line in block.split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _user_kairos_dir() -> Path:
    """``~/.kairos`` — resolved per call so tests can point a run at a temp home."""
    return Path.home() / ".kairos"


def _scope_of(path: Optional[Path], scopes: dict) -> str:
    """Which scope a skill file came from: project > global > bundled."""
    if not path:
        return "unknown"
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return "unknown"
    for label in ("project", "global", "bundled"):
        base = scopes.get(label)
        if not base:
            continue
        try:
            base_str = str(Path(base).resolve())
        except OSError:
            continue
        if resolved == base_str or resolved.startswith(base_str + os.sep):
            return label
    return "unknown"


def _bundled_tool_names(name: str) -> List[str]:
    """Tool names a bundled server exposes — without starting it."""
    from kairos.mcp_local_servers import bundled_tool_names
    return bundled_tool_names(name)


def _native_tools() -> List[str]:
    import kairos.tools as tools_mod

    names = []
    for cls in (getattr(tools_mod, "__all__", None) or []):
        obj = getattr(tools_mod, cls, None)
        # __all__ also re-exports plain helpers (checkpoint_round, ensure_repo),
        # so keep only things that actually look like a tool.
        if obj is None or not hasattr(obj, "name"):
            continue
        if not any(hasattr(obj, attr) for attr in ("run", "arun", "execute", "invoke")):
            continue
        names.append(str(obj.name))
    return sorted(set(names))
