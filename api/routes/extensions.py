"""Extensions API — list installed Skills / MCPs / Plugins.

R38.6 §27: the user asked us to "pre-install all available
MCP servers / plugins / skills" with care for dedup. We
curated a registry and wrote ``scripts/install_extensions.py``
which downloads Skills to ``kairos/skills/<name>/SKILL.md``
and writes JSON registries to ``kairos/extensions/``.

This module exposes those registries via HTTP so the UI can
show the user what's installed and what's available.

Endpoints
---------

GET /api/extensions/skills
    List installed skills. Returns the metadata from
    ``installed.json`` (status: installed / already-installed
    / download-failed) plus a small excerpt from each
    SKILL.md's frontmatter (name + description).

GET /api/extensions/skills/{name}
    Return the full SKILL.md of an installed skill.

GET /api/extensions/mcps
    List the curated MCP server registry. The backend doesn't
    actually launch them (yet) — it just exposes the config
    so the user can see what's available and what env keys
    they need to set.

GET /api/extensions/plugins
    List curated plugins (the agentic CLI marketplaces). Each entry
    has the install command (e.g. ``npx claude-plugins install ...``).

GET /api/extensions/summary
    Aggregate counts: skills installed / failed, MCPs total,
    plugins total. Used by the UI to render the header status.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Repo root is the parent of api/. The Skills / extensions
# directories live at <repo>/kairos/skills and <repo>/kairos/extensions.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILLS_DIR = _REPO_ROOT / "kairos" / "skills"
_EXT_DIR = _REPO_ROOT / "kairos" / "extensions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(name: str) -> dict:
    p = _EXT_DIR / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SkillItem(BaseModel):
    name: str
    category: Optional[str] = None
    description: Optional[str] = None  # from frontmatter
    source: Optional[str] = None
    status: str  # "installed" | "already-installed" | "download-failed" | "missing"
    bytes: Optional[int] = None
    path: Optional[str] = None  # path within repo
    on_disk: bool = False


class SkillsResponse(BaseModel):
    skills: List[SkillItem]
    total: int
    installed: int
    failed: int


class MCPItem(BaseModel):
    name: str
    category: str
    transport: str
    command: str
    args: List[str]
    description: str
    env_keys: List[str] = []
    source: Optional[str] = None
    official: bool = False
    stars: Optional[str] = None


class MCPsResponse(BaseModel):
    servers: List[MCPItem]
    total: int


class PluginItem(BaseModel):
    name: str
    marketplace: str
    install: str
    description: str


class PluginsResponse(BaseModel):
    plugins: List[PluginItem]
    total: int


class SummaryResponse(BaseModel):
    skills: dict
    mcps: dict
    plugins: dict


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/extensions/skills", response_model=SkillsResponse)
async def list_skills() -> SkillsResponse:
    """List installed skills with their frontmatter metadata.

    The source of truth is ``kairos/extensions/installed.json``
    (the install script's summary), but we also verify the
    SKILL.md is actually on disk — a stale summary can mark
    a skill as installed when the file is missing.
    """
    installed = _load_json("installed.json")
    records = (installed.get("skills") or {}).get("records") or []
    items: List[SkillItem] = []
    for r in records:
        # The record's path is the *target* file (relative to repo
        # root), e.g. "kairos/skills/docx/SKILL.md". Resolve and
        # verify on disk.
        rel_path = r.get("path") or ""
        abs_path = _REPO_ROOT / rel_path
        on_disk = abs_path.exists() and abs_path.stat().st_size > 100
        # Read the frontmatter for a richer description.
        description: Optional[str] = None
        if on_disk:
            try:
                md = abs_path.read_text(encoding="utf-8", errors="replace")
                description = _parse_frontmatter(md).get("description")
            except OSError:
                pass
        if not description:
            description = r.get("description")
        items.append(SkillItem(
            name=r.get("name", ""),
            category=r.get("category"),
            description=description,
            source=r.get("source"),
            status=r.get("status", "missing") if on_disk
                    else "missing",
            bytes=r.get("bytes"),
            path=rel_path,
            on_disk=on_disk,
        ))
    installed_count = sum(1 for i in items if i.on_disk)
    failed_count = sum(1 for i in items if i.status == "download-failed")
    return SkillsResponse(
        skills=items,
        total=len(items),
        installed=installed_count,
        failed=failed_count,
    )


@router.get("/extensions/skills/{name}")
async def get_skill(name: str) -> dict:
    """Return the full SKILL.md content for a single skill."""
    # Sanitize the name — no path traversal.
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="invalid skill name")
    path = _SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"skill not found: {name}")
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"read failed: {exc}")
    return {
        "name": name,
        "content": content,
        "bytes": path.stat().st_size,
        "frontmatter": _parse_frontmatter(content),
    }


# ---------------------------------------------------------------------------
# MCPs
# ---------------------------------------------------------------------------


@router.get("/extensions/mcps", response_model=MCPsResponse)
async def list_mcps() -> MCPsResponse:
    """List the curated MCP server registry.

    These are the MCP servers the user can launch via stdio.
    Each entry has the command + args + required env keys.
    The backend does NOT auto-launch them — the user installs
    Node.js / uv / Python deps separately and starts them
    when needed.
    """
    reg = _load_json("mcps.json")
    servers = [
        MCPItem(
            name=s["name"],
            category=s.get("category", "other"),
            transport=s.get("transport", "stdio"),
            command=s.get("command", ""),
            args=s.get("args", []),
            description=s.get("description", ""),
            env_keys=s.get("env_keys", []),
            source=s.get("source"),
            official=bool(s.get("official")),
            stars=s.get("stars"),
        )
        for s in (reg.get("servers") or [])
    ]
    return MCPsResponse(servers=servers, total=len(servers))


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


@router.get("/extensions/plugins", response_model=PluginsResponse)
async def list_plugins() -> PluginsResponse:
    reg = _load_json("plugins.json")
    plugins = [
        PluginItem(
            name=p["name"],
            marketplace=p.get("marketplace", ""),
            install=p.get("install", ""),
            description=p.get("description", ""),
        )
        for p in (reg.get("plugins") or [])
    ]
    return PluginsResponse(plugins=plugins, total=len(plugins))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@router.get("/extensions/summary", response_model=SummaryResponse)
async def summary() -> SummaryResponse:
    """Aggregate counts for the UI header.

    Skills counts are computed from the on-disk ``kairos/skills/``
    directory (so a partial install is correctly reflected) and
    cross-referenced with the install records.
    """
    installed = _load_json("installed.json")
    skills_section = installed.get("skills") or {}
    # On-disk truth: count actual SKILL.md files in kairos/skills/.
    on_disk_skills: List[str] = []
    if _SKILLS_DIR.is_dir():
        for d in _SKILLS_DIR.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists():
                on_disk_skills.append(d.name)
    mcp_reg = _load_json("mcps.json")
    plugin_reg = _load_json("plugins.json")
    return SummaryResponse(
        skills={
            "total": len(skills_section.get("records") or []),
            "installed_on_disk": len(on_disk_skills),
            "recorded_installed": skills_section.get("installed", 0),
            "failed": skills_section.get("failed", 0),
        },
        mcps={
            "total": len(mcp_reg.get("servers") or []),
        },
        plugins={
            "total": len(plugin_reg.get("plugins") or []),
        },
    )
