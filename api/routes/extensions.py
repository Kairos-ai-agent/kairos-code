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

import asyncio
import json
import logging
import os
import re
import shutil
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
    #: R38.11: served by this install — no npx, no uvx, nothing to download.
    bundled: bool = False
    #: False when a bundled server works with the network unplugged.
    needs_network: bool = True


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

    Each entry has the command + args + required env keys. Entries flagged
    ``bundled`` are served by *this* install (:mod:`kairos.mcp_local_servers`
    and :mod:`kairos.mcp_filesystem_server`): they need no Node.js, no uv and
    nothing downloaded, and the command shown here is the one that will run.
    The rest are upstream invocations (``npx``/``uvx``) that fetch a package at
    first use, which is why they carry ``needsNetwork: true``.
    """
    from kairos.mcp_local_servers import resolve_bundled

    reg = _load_json("mcps.json")
    servers = []
    for s in (reg.get("servers") or []):
        command = s.get("command", "")
        args = list(s.get("args", []))
        bundled = bool(s.get("bundled"))
        if bundled:
            resolved = resolve_bundled(str(s.get("name", "")))
            if resolved:
                command = resolved["command"]
                args = list(resolved["args"])
        servers.append(
            MCPItem(
                name=s["name"],
                category=s.get("category", "other"),
                transport=s.get("transport", "stdio"),
                command=command,
                args=args,
                description=s.get("description", ""),
                env_keys=s.get("env_keys", []),
                source=s.get("source"),
                official=bool(s.get("official")),
                stars=s.get("stars"),
                bundled=bundled,
                needs_network=bool(s.get("needsNetwork", not bundled)),
            )
        )
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




# ---------------------------------------------------------------------------
# R38.12 ⑦ — the capability view
#
# /summary counts registry records. That is a description of the install, not
# of the running system, and it is exactly how a shipped-but-unreachable bug
# stays invisible: the registry said "26 skills installed" while the loader
# returned nothing at all. Everything here is read from the objects the runtime
# itself uses — SkillsLoader, McpRegistry, PluginManager — and anything that
# cannot be reached is named in `problems` instead of being dropped.
# ---------------------------------------------------------------------------

_HOME_GLOBAL_SKILLS = Path.home() / ".kairos" / "skills"


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


def _project_root(project_id: Optional[str]) -> Optional[Path]:
    """The project's working directory, or None when it cannot be resolved."""
    if not project_id:
        return None
    try:
        from api.routes.workbench import _orch  # shared orchestrator accessor
        project = _orch().get_project(project_id)
    except Exception as exc:  # not fatal: the other scopes still report
        logger.debug("capabilities: project %s unresolvable: %s", project_id, exc)
        return None
    root = getattr(project, "work_dir", None) or getattr(project, "workspace", None)
    if not root:
        return None
    try:
        return Path(root).resolve()
    except OSError:
        return None


def _skills_section(project_root: Optional[Path]) -> dict:
    from kairos.skills import SkillsLoader

    scopes = {
        "project": (project_root / ".kairos" / "skills") if project_root else None,
        "global": _HOME_GLOBAL_SKILLS,
        "bundled": _SKILLS_DIR,
    }
    loader = SkillsLoader(
        project_dir=scopes["project"],
        global_dir=scopes["global"],
        bundled_dir=scopes["bundled"],
    )
    skills = loader.discover()

    on_disk: List[str] = []
    if _SKILLS_DIR.is_dir():
        for path in sorted(_SKILLS_DIR.rglob("*.md")):
            on_disk.append(path.relative_to(_SKILLS_DIR).as_posix())

    items = []
    by_scope = {"project": 0, "global": 0, "bundled": 0, "unknown": 0}
    for skill in skills:
        scope = _scope_of(skill.source_path, scopes)
        by_scope[scope] = by_scope.get(scope, 0) + 1
        items.append({
            "name": skill.name,
            "scope": scope,
            "priority": skill.priority,
            "when": dict(skill.when or {}),
            "description": (skill.description or "")[:200],
            "path": str(skill.source_path) if skill.source_path else "",
        })
    items.sort(key=lambda s: (-s["priority"], s["name"]))
    return {
        "count": len(items),
        "byScope": by_scope,
        "filesOnDisk": len(on_disk),
        "items": items,
    }


def _bundled_tool_names(name: str) -> List[str]:
    """Tool names a bundled server exposes — without starting it."""
    from kairos.mcp_local_servers import bundled_tool_names
    return bundled_tool_names(name)


def _mcp_section(project_root: Optional[Path], probe: bool) -> dict:
    from kairos.mcp_client import McpRegistry, audit_configs
    from kairos.mcp_local_servers import BUNDLED_SERVERS

    registry_json = _load_json("mcps.json")
    bundled_in_registry = {
        str(s.get("name")) for s in (registry_json.get("servers") or [])
        if s.get("bundled")
    }

    home = Path.home() / ".kairos"
    try:
        audit = audit_configs(project_dir=project_root, user_dir=home)
        configs = audit["servers"]
        rejected = dict(audit.get("rejected") or {})
    except Exception as exc:  # a broken mcp.yaml must not 500 the whole view
        logger.warning("capabilities: mcp config unreadable: %s", exc)
        configs, rejected = {}, {}

    configured = []
    for name, cfg in sorted(configs.items()):
        entry = {
            "name": name,
            "transport": cfg.transport,
            "enabled": bool(cfg.enabled),
            "bundled": name in bundled_in_registry or name in BUNDLED_SERVERS,
            "source": "config",
            "needsNetwork": cfg.transport in ("http", "sse"),
            # Header NAMES only. A config file legitimately holds a token, and
            # this endpoint must never echo one back.
            "headerNames": sorted((cfg.headers or {}).keys()),
        }
        if cfg.transport in ("http", "sse"):
            entry["url"] = cfg.url
        else:
            entry["command"] = cfg.command
            entry["resolvable"] = bool(
                cfg.command and (
                    os.path.isabs(cfg.command) or shutil.which(cfg.command)))
        configured.append(entry)

    # The servers this install ships and can run with no network: their tools are
    # known without starting anything, so the view is honest even in `probe=false`.
    bundled = []
    for name in BUNDLED_SERVERS:
        tool_names = _bundled_tool_names(name)
        bundled.append({
            "name": name,
            "offline": True,
            "toolCount": len(tool_names),
            "tools": tool_names,
        })

    section = {
        "configured": configured,
        "rejected": rejected,
        "bundledServers": bundled,
        "startupErrors": {},
        "probed": bool(probe),
    }

    if probe:
        # Spawning servers is a side effect, so it only happens on request.
        registry = McpRegistry()
        try:
            registry.load(project_dir=project_root, user_dir=home)
            tools = registry.start_all()
            section["liveTools"] = sorted(t.name for t in tools)
        except Exception as exc:
            section["probeError"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                asyncio.run(registry.close_all())
            except Exception as exc:
                logger.debug("capabilities: close_all: %s", exc)
        section["startupErrors"] = dict(registry.startup_errors() or {})

    return section


def _plugins_section() -> dict:
    from kairos.plugins import PluginManager

    manager = PluginManager()
    installed: List[dict] = []
    for info in manager.list_installed():
        entry = info.to_dict()
        entry["compatible"] = getattr(info, "compatible", True)
        entry["capabilities"] = list(getattr(info, "capabilities", []) or [])
        installed.append(entry)
    # Two different numbers that both matter: what is installed *here* versus
    # what this build knows how to install. Reporting only the first reads as
    # "no plugins exist" on a fresh install.
    registry = _load_json("plugins.json")
    available = registry.get("plugins") or []
    return {
        "installed": installed,
        "count": len(installed),
        "availableInRegistry": len(available),
    }


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


@router.get("/extensions/capabilities")
async def capabilities(project_id: Optional[str] = None,
                       probe: bool = False) -> dict:
    """What the agent will actually have here, and why the rest is missing.

    ``probe=true`` starts the configured MCP servers to report live tool counts
    and startup errors; it is off by default because spawning a server is a side
    effect, not a read.
    """
    project_root = _project_root(project_id)
    skills = _skills_section(project_root)
    mcp = _mcp_section(project_root, probe)
    plugins = _plugins_section()
    native = _native_tools()

    problems: List[str] = []
    if skills["filesOnDisk"] and not skills["count"]:
        problems.append(
            f"{skills['filesOnDisk']} skill files are on disk but the loader "
            "returned none — the bundled skills are unreachable")
    if skills["byScope"].get("bundled", 0) == 0 and _SKILLS_DIR.is_dir():
        problems.append("no bundled skills loaded from " + str(_SKILLS_DIR))
    for entry in mcp["configured"]:
        if not entry["enabled"]:
            problems.append(f"mcp server {entry['name']}: disabled")
        elif entry["transport"] == "stdio" and not entry.get("resolvable"):
            problems.append(
                f"mcp server {entry['name']}: command {entry['command']!r} not on PATH")
        elif entry["transport"] in ("http", "sse") and not entry.get("url"):
            problems.append(f"mcp server {entry['name']}: no url")
    for name, reason in (mcp.get("rejected") or {}).items():
        # Configured but dropped before it ever ran — the exact class of
        # silent failure this view exists to surface.
        problems.append(f"mcp server {name}: configured but not usable ({reason})")
    for name, err in (mcp.get("startupErrors") or {}).items():
        problems.append(f"mcp server {name} failed to start: {err}")
    for entry in plugins["installed"]:
        if not entry.get("compatible", True):
            problems.append(
                f"plugin {entry.get('name')}: incompatible ({entry.get('reason') or 'version'})")
    installed = _load_json("installed.json")
    for section, records in (("skill", (installed.get("skills") or {}).get("records") or []),):
        for record in records:
            status = str(record.get("status") or "")
            if status in ("failed", "source-missing"):
                problems.append(
                    f"{section} {record.get('name')}: {status} ({record.get('source') or record.get('path') or ''})")
    if project_id and project_root is None:
        problems.append(f"project {project_id} could not be resolved; project scope not scanned")

    return {
        "skills": skills,
        "mcp": mcp,
        "plugins": plugins,
        "nativeTools": native,
        "problems": problems,
        "scanned": {
            "projectId": project_id,
            "projectRoot": str(project_root) if project_root else None,
            "bundledSkillsDir": str(_SKILLS_DIR),
        },
    }

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
