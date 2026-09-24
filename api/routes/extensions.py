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

Remote marketplaces (the extensions page)
------------------------------------------

GET /api/extensions/market/sources
    Every source this build can reach — the curated-adjacent official MCP
    registry, Smithery, Cline's three listings, and Anthropic's official plugin
    and skill directories. Each item carries ``id``, ``label``, ``kind`` (mcp /
    plugin / skill / mixed), ``needs_query``, ``homepage``, ``total`` (the
    upstream's own count, or null when it declares none) and ``note``. The
    totals are fetched in parallel, off the event loop, with a timeout each.

GET /api/extensions/market/search?source=ID&q=QUERY&limit=N
    One page of one source: ``source``, ``total``, ``items`` (also under its
    older name ``entries``), ``note`` and ``error``. An empty result set and a
    source that could not be reached are deliberately different: the first is
    ``ok: true`` with ``total: 0``, the second is ``ok: false`` with a reason in
    ``error`` and ``total: null``.

POST /api/extensions/market/install
    Install one resolved entry by its own kind: an MCP server into ``mcp.yaml``
    (the same path a curated install takes), a Claude plugin by conversion into
    the plugin directory, a skill as a SKILL.md in the skills scope. Answers
    ``ok``, ``changed``, ``kind``, ``installed_path`` and ``warnings``. It is
    idempotent, and a conversion that had to skip a file says so in ``warnings``
    by name.
"""
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
    installed: bool = False  # on disk, so the loader will actually load it
    scope: Optional[str] = None  # project | global | bundled


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


# R38.13 — install / uninstall / probe. The registry above says what is
# *available*; these say what is *installed* and whether it actually runs. The
# split matters: an entry can be in the registry, written into mcp.yaml, and
# still never start (command not on PATH, missing credential, disabled), and
# "installed" without that distinction is how a dead server looks fine.


class InstalledMCPServer(BaseModel):
    name: str
    #: Which layer the entry came from: bundled / user / project.
    layer: str
    transport: str
    enabled: bool
    #: True only when the runtime would really load and start it.
    will_run: bool
    in_registry: bool = False
    bundled: bool = False
    command: Optional[str] = None
    args: List[str] = []
    url: Optional[str] = None
    #: Environment variable NAMES only — never a value from the user's config.
    env_keys: List[str] = []
    command_found: Optional[bool] = None
    problem: Optional[str] = None


class InstalledMCPsResponse(BaseModel):
    servers: List[InstalledMCPServer]
    total: int
    enabled: int
    will_run: int
    layers: dict
    paths: dict
    problems: List[str] = []


class MCPInstallRequest(BaseModel):
    name: str
    scope: str = "user"  # "user" | "project"
    project_id: Optional[str] = None


class MCPInstallResponse(BaseModel):
    ok: bool
    changed: bool
    path: str
    scope: str
    entry: dict
    config: dict
    #: The server's effective config after the install.
    server: Optional[InstalledMCPServer] = None


class MCPUninstallRequest(BaseModel):
    name: str
    scope: str = "user"
    project_id: Optional[str] = None


class MCPUninstallResponse(BaseModel):
    ok: bool
    changed: bool
    path: str
    scope: str
    entry: dict = {}


class MCPProbeRequest(BaseModel):
    name: str
    #: Which layer to start. Omitted = the effective config, i.e. what the
    #: runtime itself would start here.
    scope: Optional[str] = None
    project_id: Optional[str] = None


class MCPProbeResponse(BaseModel):
    ok: bool
    name: str
    tools: List[str] = []
    error: Optional[str] = None
    ms: int = 0


class MarketSourceItem(BaseModel):
    """One place an extension can come from, and what it can give you."""

    id: str
    label: str
    homepage: Optional[str] = None
    #: "mcp" | "plugin" | "skill" | "mixed".
    kind: str = "mcp"
    description: str = ""
    installable: bool = False
    #: True for a source too large to browse that searches on its own side (the
    #: registry, Smithery): the page asks for a query before it asks for a page.
    needs_query: bool = False
    #: How many entries the upstream itself declares. ``None`` when it declares
    #: none — the official registry reports a page cursor, not a total — or when
    #: it could not be asked. Never a number this server made up.
    total: Optional[int] = None
    #: A caveat worth showing beside the source: what is listed but not
    #: installable, or what installing actually does.
    note: Optional[str] = None


class MarketSourcesResponse(BaseModel):
    sources: List[MarketSourceItem] = []


class MarketEntry(BaseModel):
    """A remote entry, normalised to the same shape the curated registry uses.

    ``installable`` is the honest field: an entry with neither a launcher nor a
    URL is still worth showing, and the page links out instead of offering an
    Install button that cannot work.

    ``kind`` says what it is — an MCP server, a plugin or a skill. It matters
    because the three install differently (a YAML key, a directory, a SKILL.md),
    and offering a plugin as an MCP install is how a user ends up with a config
    entry that never starts. ``note`` carries the reason when something cannot be
    installed.
    """

    name: str
    source: str
    upstream: Optional[str] = None
    description: str = ""
    version: Optional[str] = None
    category: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: List[str] = []
    url: Optional[str] = None
    env_keys: List[str] = []
    homepage: Optional[str] = None
    installable: bool = False
    #: "mcp" | "plugin" | "skill".
    kind: str = "mcp"
    note: Optional[str] = None


class MarketSearchResponse(BaseModel):
    """One page of one remote source.

    ``entries`` and ``items`` are the same list under two names: the marketplace
    page reads ``entries`` and the extensions page reads ``items``, and renaming
    the field would empty one of them without a word.

    ``error`` is the important field. An empty result set and a source that
    could not be reached must not look the same: ``ok: false`` plus a reason is
    the difference, and ``total: null`` (rather than ``0``) keeps "we could not
    ask" from reading as "there are none".
    """

    ok: bool = True
    source: str = ""
    total: Optional[int] = None
    entries: List[MarketEntry] = []
    items: List[MarketEntry] = []
    #: What the source says about its own listing ("not installable here", ...).
    note: Optional[str] = None
    error: Optional[str] = None


class MarketInstallRequest(BaseModel):
    source: str
    id: str
    scope: str = "user"
    project_id: Optional[str] = None


class MarketInstallResponse(BaseModel):
    """The result of installing one remote entry.

    ``installed_path`` is where it landed — an ``mcp.yaml``, a plugin directory,
    or a SKILL.md — and ``kind`` says which of the three it was. ``warnings`` is
    always present and is the point: a converted plugin that had to leave a file
    behind says so here, by name. An install that quietly dropped half of what it
    fetched would be the defect this field exists to prevent.

    The MCP-specific fields (``path``, ``entry``, ``config``, ``server``) are
    kept for the mcp.yaml writers; for a plugin or a skill they are the entry and
    otherwise empty.
    """

    ok: bool
    changed: bool
    kind: str = "mcp"
    installed_path: Optional[str] = None
    warnings: List[str] = []
    source: str = ""
    id: str = ""
    scope: str = "user"
    path: Optional[str] = None
    entry: dict = {}
    config: dict = {}
    #: The server's effective config after an MCP install.
    server: Optional[InstalledMCPServer] = None


class PluginItem(BaseModel):
    name: str
    marketplace: str = ""
    install: str = ""
    description: str = ""
    installed: bool = False
    origin: Optional[str] = None  # bundled | user | registry
    path: Optional[str] = None
    version: Optional[str] = None
    enabled: Optional[bool] = None
    capabilities: List[str] = []


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
    """List the skills that are installed, and where each one came from.

    The source of truth is the loader — bundled, then project, then global —
    because that is the set the agent will actually have.  A record file
    (``kairos/extensions/installed.json``) describes one install run, not the
    running system, and reading it alone is how a skill that is on disk stays
    invisible: the summary predates everything added since.  Records are still
    honoured in both directions — a record whose file is gone is reported as
    missing rather than dropped, and a file whose record is stale is reported
    as installed, because it is.
    """
    from kairos.skills import SkillsLoader

    scopes = {"project": None, "global": _HOME_GLOBAL_SKILLS,
              "bundled": _SKILLS_DIR}
    loader = SkillsLoader(project_dir=None, global_dir=scopes["global"],
                          bundled_dir=scopes["bundled"])

    records: dict = {}
    for r in ((_load_json("installed.json").get("skills") or {})
              .get("records") or []):
        name = r.get("name")
        if name and name not in records:
            records[name] = r

    items: List[SkillItem] = []
    seen = set()
    for skill in loader.discover():
        rec = records.get(skill.name) or {}
        path = skill.source_path
        size: Optional[int] = None
        if path is not None:
            try:
                size = path.stat().st_size
                size = size if size > 0 else None
            except OSError:
                path = None
        scope = _scope_of(path, scopes)
        if path is not None:
            try:
                where = path.relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                where = str(path)
        else:
            where = rec.get("path")
        items.append(SkillItem(
            name=skill.name,
            category=getattr(skill, "category", "") or rec.get("category"),
            description=skill.description or rec.get("description"),
            source=rec.get("source")
                   or ("bundled" if scope == "bundled" else None),
            status=rec.get("status")
                   or ("installed" if path is not None else "missing"),
            bytes=size if size is not None else rec.get("bytes"),
            path=where,
            on_disk=path is not None,
            installed=path is not None,
            scope=scope,
        ))
        seen.add(skill.name)

    # A record whose file is gone is a broken install. Reporting it is what
    # keeping records around is for.
    for name, rec in records.items():
        if name in seen:
            continue
        items.append(SkillItem(
            name=str(name),
            category=rec.get("category"),
            description=rec.get("description"),
            source=rec.get("source"),
            status="missing",
            bytes=rec.get("bytes"),
            path=rec.get("path"),
            on_disk=False,
            installed=False,
            scope=None,
        ))

    # 583 skills is a lot of list: keep it in a stable, findable order rather
    # than whatever order the loader happened to walk the directories in.
    items.sort(key=lambda i: i.name)

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
# R38.13 — the installed view
#
# The registry answers "what is available?"; this answers "what is here, from
# which layer, and will it actually start?". Everything is read through the
# runtime's own loader (``mcp_client._merged_servers`` / ``_build_config``), so
# the list cannot disagree with what the agent gets.
# ---------------------------------------------------------------------------

#: ``McpServerConfig.source`` → the layer name the UI uses.
_LAYER_OF_SOURCE = {"bundled-plugin": "bundled", "user": "user", "project": "project"}


def _user_kairos_dir() -> Path:
    """``~/.kairos`` — resolved per call so tests can point a run at a temp home."""
    return Path.home() / ".kairos"


def _installed_registry() -> dict:
    """``{name: entry}`` from the curated registry."""
    reg = _load_json("mcps.json")
    return {str(s.get("name")): s for s in (reg.get("servers") or [])
            if isinstance(s, dict) and s.get("name")}


def _effective_mcp(project_root: Optional[Path]) -> dict:
    """Every MCP server the runtime would load here, with its layer and its odds.

    ``will_run`` is the honest field: an entry that is enabled, buildable *and*
    whose command resolves on this machine. The rest of the state — disabled,
    rejected by the loader, a command that is not on PATH — lands in ``problem``
    instead of being silently dropped.
    """
    from kairos.mcp_client import _build_config, _merged_servers
    from kairos.mcp_local_servers import BUNDLED_SERVERS

    user_dir = _user_kairos_dir()
    registry = _installed_registry()
    bundled_names = {n for n, e in registry.items() if e.get("bundled")}
    try:
        merged = _merged_servers(project_root, user_dir)
    except Exception as exc:  # a broken mcp.yaml must not 500 the whole view
        logger.warning("extensions: mcp config unreadable: %s", exc)
        merged = {}

    servers: List[InstalledMCPServer] = []
    problems: List[str] = []
    by_layer = {"bundled": 0, "user": 0, "project": 0}
    enabled = will_run = 0

    for raw_name in sorted(merged, key=str):
        name = str(raw_name)
        raw = merged[raw_name]
        cfg, problem = _build_config(name, raw)
        layer = _LAYER_OF_SOURCE.get(str(raw.get("_source") or ""), "config")
        by_layer[layer] = by_layer.get(layer, 0) + 1
        if cfg is None:
            # Configured but unusable. Still listed, with the reason.
            item = InstalledMCPServer(
                name=name,
                layer=layer,
                transport=str(raw.get("transport") or "stdio"),
                enabled=bool(raw.get("enabled", True)),
                will_run=False,
                in_registry=name in registry,
                bundled=name in bundled_names or name in BUNDLED_SERVERS,
                command=(str(raw["command"]) if raw.get("command") else None),
                args=[str(a) for a in (raw.get("args") or [])],
                url=(str(raw["url"]) if raw.get("url") else None),
                env_keys=sorted(str(k) for k in (raw.get("env") or {})),
                problem=problem,
            )
        else:
            is_enabled = bool(cfg.enabled)
            if cfg.transport in ("http", "sse"):
                found: Optional[bool] = None
                keys = sorted((cfg.headers or {}).keys())
            else:
                found = bool(cfg.command and (
                    os.path.isabs(cfg.command) or shutil.which(cfg.command)))
                keys = sorted((cfg.env or {}).keys())
            runs = bool(is_enabled and (found is None or found))
            item = InstalledMCPServer(
                name=name,
                layer=layer,
                transport=cfg.transport,
                enabled=is_enabled,
                will_run=runs,
                in_registry=name in registry,
                bundled=name in bundled_names or name in BUNDLED_SERVERS,
                command=cfg.command or None,
                args=list(cfg.args or []),
                url=cfg.url or None,
                env_keys=keys,
                command_found=found,
                problem=None if runs else (
                    "disabled" if not is_enabled
                    else f"command {cfg.command!r} is not on PATH"),
            )
        servers.append(item)
        enabled += 1 if item.enabled else 0
        will_run += 1 if item.will_run else 0
        if item.problem and item.enabled:
            problems.append(f"{name}: {item.problem}")

    return {
        "servers": servers,
        "total": len(servers),
        "enabled": enabled,
        "will_run": will_run,
        "layers": by_layer,
        "paths": {
            "user": str(user_dir / "mcp.yaml"),
            "project": (str(project_root / ".kairos" / "mcp.yaml")
                        if project_root else None),
        },
        "problems": problems,
    }


def _write_scope(scope: str, project_id: Optional[str]) -> Optional[Path]:
    """The project root a write goes to, or ``None`` for the user layer.

    400s on an unknown scope and 404s on a project that cannot be resolved —
    writing a project file to guesswork would be worse than refusing.
    """
    if scope not in ("user", "project"):
        raise HTTPException(status_code=400,
                            detail=f"unknown scope {scope!r}: expected 'user' or 'project'")
    if scope == "user":
        return None
    if not project_id:
        raise HTTPException(status_code=400,
                            detail="scope='project' needs a project_id")
    root = _project_root(project_id)
    if root is None:
        raise HTTPException(status_code=404,
                            detail=f"project {project_id} could not be resolved")
    return root


def _effective_one(name: str, project_root: Optional[Path]) -> Optional[InstalledMCPServer]:
    section = _effective_mcp(project_root)
    for item in section["servers"]:
        if item.name == name:
            return item
    return None


@router.get("/extensions/mcp/installed", response_model=InstalledMCPsResponse)
async def installed_mcps(project_id: Optional[str] = None) -> InstalledMCPsResponse:
    """The effective MCP config: every server, its layer, and whether it runs."""
    return InstalledMCPsResponse(**_effective_mcp(_project_root(project_id)))


# ---------------------------------------------------------------------------
# R38.13 — install / uninstall / probe
# ---------------------------------------------------------------------------

#: How long ONE server gets to answer the initialize handshake. A user is
#: waiting on this single probe, so it is deliberately not the whole-registry
#: budget (``mcp_client.START_BUDGET_S``, 60s): a server that cannot say hello
#: in 25s is broken here, and saying so quickly is the useful answer.
PROBE_TIMEOUT_S = 25.0


def _probe_config(name: str, scope: Optional[str],
                  project_root: Optional[Path]):
    """``(config, problem)`` for the server a probe should start.

    With a scope, exactly that layer's entry is used — the one the user just
    installed. Without one, the effective config (what the runtime would start)
    is used. Never a registry entry that is not installed: probing something the
    runtime will not load proves nothing about this machine's setup.
    """
    from kairos.mcp_client import _build_config, _load_yaml_config, _merged_servers

    if scope:
        if scope not in ("user", "project"):
            raise HTTPException(status_code=400,
                                detail=f"unknown scope {scope!r}: expected 'user' or 'project'")
        if scope == "user":
            path = _user_kairos_dir() / "mcp.yaml"
        else:
            if project_root is None:
                raise HTTPException(status_code=400,
                                    detail="scope='project' needs a resolvable project_id")
            path = project_root / ".kairos" / "mcp.yaml"
        raw = (_load_yaml_config(path).get("mcp_servers") or {}).get(name)
        if not isinstance(raw, dict):
            return None, f"{name!r} is not configured in the {scope} layer ({path})"
        return _build_config(name, {**raw, "name": name, "_source": scope})

    merged = _merged_servers(project_root, _user_kairos_dir())
    raw = merged.get(name)
    if not isinstance(raw, dict):
        return None, (f"{name!r} is not configured here — install it first "
                      "(POST /api/extensions/mcp/install)")
    return _build_config(name, raw)


async def _probe_server(config) -> dict:
    """Spawn one server, complete ``initialize``, and ask for its tool list.

    This is the difference between "the config looks right" and "it works here":
    the command is really executed, the handshake is really sent, and the tools
    that come back are the ones the agent would get.
    """
    from kairos.mcp_client import McpError, client_for

    client = client_for(config, budget_s=PROBE_TIMEOUT_S)
    started = time.monotonic()
    ok, tools, error = False, [], None
    try:
        await client.start()  # spawn + the MCP initialize handshake
        schemas = await client.list_tools()
        tools = [str(s.get("name")) for s in schemas
                 if isinstance(s, dict) and s.get("name")]
        ok = True
    except McpError as exc:
        error = str(exc)
    except Exception as exc:  # defensive: a probe must never 500
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await client.close()
        except Exception as exc:
            logger.debug("extensions: probe close failed: %s", exc)
    return {"ok": ok, "tools": tools, "error": error,
            "ms": int((time.monotonic() - started) * 1000)}


#: How long one source gets to say how big it is. The sources list is a
#: read-mostly page: a source that cannot answer in a few seconds contributes
#: ``total: null`` rather than holding up every other source.
MARKET_TOTAL_TIMEOUT_S = 6.0


async def _source_total(source_id: str) -> Optional[int]:
    """One source's own declared size, off the event loop.

    ``total_count`` never raises and never invents: a source that has no total
    (the registry publishes a cursor) or cannot be reached answers ``None``, and
    the page shows the source without a count.
    """
    from kairos.extensions import market_sources as ms

    try:
        return await asyncio.wait_for(asyncio.to_thread(ms.total_count, source_id),
                                      MARKET_TOTAL_TIMEOUT_S)
    except Exception:  # a timeout, a dead source, a changed shape
        return None


@router.get("/extensions/market/sources", response_model=MarketSourcesResponse)
async def market_sources() -> MarketSourcesResponse:
    """The remote marketplaces this build can reach, and what each one holds.

    The list itself is declared rather than probed — a list that goes empty when
    the network hiccups is worse than a list that is always there — but each
    source's ``total`` is asked for, in parallel and off the event loop, because
    a number the user can act on is worth one bounded request. A source that
    does not answer in time is reported with ``total: null``.
    """
    from kairos.extensions import market_sources as ms

    metas = ms.list_sources()
    totals = await asyncio.gather(*[_source_total(meta["id"]) for meta in metas])
    items = []
    for meta, total in zip(metas, totals):
        merged = dict(meta)
        merged["total"] = total
        items.append(MarketSourceItem(**merged))
    return MarketSourcesResponse(sources=items)


@router.get("/extensions/market/search", response_model=MarketSearchResponse)
async def market_search(source: str, q: Optional[str] = None,
                        limit: int = 30) -> MarketSearchResponse:
    """Search one remote marketplace.

    A source that cannot be reached says so in ``error`` instead of returning an
    empty list that looks like "no results" — those two are different answers and
    the page has to be able to tell them apart. Every source fetches its entries
    off the event loop: one slow marketplace must not stall the service.
    """
    from kairos.extensions import market_sources as ms

    result = await asyncio.to_thread(ms.search, source, query=q, limit=limit)
    entries: List[MarketEntry] = []
    for raw in (result.get("entries") or []):
        if isinstance(raw, dict) and raw.get("name"):
            entries.append(MarketEntry(**raw))
    return MarketSearchResponse(
        ok=bool(result.get("ok", True)),
        source=str(result.get("source") or source),
        total=result.get("total"),
        entries=entries,
        items=entries,
        note=result.get("note"),
        error=result.get("error"),
    )


@router.post("/extensions/market/install", response_model=MarketInstallResponse)
async def market_install(request: MarketInstallRequest) -> MarketInstallResponse:
    """Resolve one remote entry and install it by its own kind.

    The entry is resolved again here rather than trusted from the client: the
    page's copy came over the wire once, and it is the user's file that gets
    written. An MCP entry goes into ``mcp.yaml`` through the same path a curated
    entry takes; a plugin is converted into a plugin directory; a skill is
    written into the user's skills scope. ``kind`` decides, and the response says
    which one ran.
    """
    from kairos.extensions import market_sources as ms

    entry, error = await asyncio.to_thread(ms.fetch_entry, request.source, request.id)
    if entry is None:
        raise HTTPException(status_code=400, detail=error or "entry not found")

    kind = str(entry.get("kind") or "mcp").strip().lower()
    if not entry.get("installable"):
        raise HTTPException(
            status_code=400,
            detail=(entry.get("note") or
                    "%r has no start command or URL in %s, so there is nothing to "
                    "install — open it on its homepage instead"
                    % (request.id, request.source)),
        )

    project_root = _write_scope(request.scope, request.project_id)

    if kind in ("plugin", "skill"):
        from kairos.extensions_install import install_plugin, install_skill

        installer = install_plugin if kind == "plugin" else install_skill
        try:
            result = await asyncio.to_thread(
                installer, entry, scope=request.scope, project_path=project_root,
                user_dir=_user_kairos_dir())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500,
                                detail="could not write the %s: %s" % (kind, exc))
        if not result.get("ok"):
            raise HTTPException(
                status_code=400,
                detail="; ".join(result.get("warnings") or [])
                       or "the %s could not be installed" % kind)
        return MarketInstallResponse(
            ok=True,
            changed=bool(result.get("changed")),
            kind=kind,
            installed_path=result.get("installed_path"),
            warnings=list(result.get("warnings") or []),
            source=request.source,
            id=str(entry.get("name") or request.id),
            scope=request.scope,
            path=result.get("path"),
            entry=dict(result.get("entry") or entry),
        )

    from kairos.extensions_install import install_entry

    try:
        result = install_entry(entry, name=entry["name"], scope=request.scope,
                               project_path=project_root,
                               user_dir=_user_kairos_dir())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"could not write mcp.yaml: {exc}")
    return MarketInstallResponse(
        ok=bool(result["ok"]),
        changed=bool(result["changed"]),
        kind="mcp",
        installed_path=str(result["path"]),
        warnings=[],
        source=request.source,
        id=str(entry.get("name") or request.id),
        scope=request.scope,
        path=str(result["path"]),
        entry=dict(result["entry"]),
        config=dict(result["config"]),
        server=_effective_one(entry["name"], project_root),
    )


@router.post("/extensions/mcp/install", response_model=MCPInstallResponse)
async def install_mcp_server(request: MCPInstallRequest) -> MCPInstallResponse:
    """Add one registry server to the user or project ``mcp.yaml``.

    The file's other entries and comments are left alone, the write is atomic,
    and installing the same server twice reports ``changed: false`` instead of
    rewriting the file. The response carries the server's effective config.
    """
    from kairos.extensions_install import install_mcp

    project_root = _write_scope(request.scope, request.project_id)
    try:
        result = install_mcp(request.name, scope=request.scope,
                             project_path=project_root,
                             user_dir=_user_kairos_dir())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"could not write mcp.yaml: {exc}")
    return MCPInstallResponse(
        ok=bool(result["ok"]),
        changed=bool(result["changed"]),
        path=str(result["path"]),
        scope=request.scope,
        entry=dict(result["entry"]),
        config=dict(result["config"]),
        server=_effective_one(request.name, project_root),
    )


@router.post("/extensions/mcp/uninstall", response_model=MCPUninstallResponse)
async def uninstall_mcp_server(request: MCPUninstallRequest) -> MCPUninstallResponse:
    """Remove one server key from the user or project ``mcp.yaml``.

    Absent key → ``changed: false``; a layer with no file at all is a no-op, not
    a new empty file.
    """
    from kairos.extensions_install import uninstall_mcp

    project_root = _write_scope(request.scope, request.project_id)
    try:
        result = uninstall_mcp(request.name, scope=request.scope,
                               project_path=project_root,
                               user_dir=_user_kairos_dir())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"could not write mcp.yaml: {exc}")
    return MCPUninstallResponse(
        ok=bool(result["ok"]),
        changed=bool(result["changed"]),
        path=str(result["path"]),
        scope=request.scope,
        entry=dict(result["entry"]),
    )


@router.post("/extensions/mcp/probe", response_model=MCPProbeResponse)
async def probe_mcp_server(request: MCPProbeRequest) -> MCPProbeResponse:
    """Start this one server and make it answer the MCP handshake.

    An install that was never started is a guess — the command may be missing,
    the package may not exist, the credential may be unset. The answer here comes
    from a real subprocess: it is spawned, sent ``initialize``, and asked for its
    tools. A failure is reported as ``ok: false`` with the reason (and a
    config we refuse to even try is reported the same way).
    """
    project_root = _project_root(request.project_id)
    config, problem = _probe_config(request.name, request.scope, project_root)
    if config is None:
        return MCPProbeResponse(ok=False, name=request.name,
                                tools=[], error=problem, ms=0)
    outcome = await _probe_server(config)
    return MCPProbeResponse(name=request.name, **outcome)


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


@router.get("/extensions/plugins", response_model=PluginsResponse)
async def list_plugins() -> PluginsResponse:
    """List what this build ships, what the user added, and what the registry
    could still install — each one saying which of the three it is.

    ``plugins.json`` is a catalogue of what *can* be installed, not a list of
    what is: read alone it shows a plugin as present because an entry exists for
    it, and shows nothing at all for a plugin that arrived from anywhere else.
    Disk is the truth; the registry is the shelf.
    """
    from kairos.plugins import PluginManager

    manager = PluginManager()

    def from_info(info) -> PluginItem:
        entry = info.to_dict() if hasattr(info, "to_dict") else {}
        return PluginItem(
            name=str(entry.get("name") or getattr(info, "name", "")),
            description=entry.get("description") or "",
            installed=True,
            origin="bundled" if entry.get("bundled") else "user",
            path=entry.get("path"),
            version=entry.get("version"),
            enabled=entry.get("enabled"),
            capabilities=list(entry.get("capabilities") or []),
        )

    items: List[PluginItem] = []
    seen = set()
    for getter in ("list_bundled", "list_installed"):
        try:
            found = getattr(manager, getter)()
        except Exception as exc:  # one broken manifest must not hide the rest
            logger.warning("plugins: %s failed: %s", getter, exc)
            continue
        for info in found:
            name = getattr(info, "name", "")
            if not name or name in seen:
                continue
            seen.add(name)
            items.append(from_info(info))

    for p in (_load_json("plugins.json").get("plugins") or []):
        name = p.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(PluginItem(
            name=name,
            marketplace=p.get("marketplace", ""),
            install=p.get("install", ""),
            description=p.get("description", ""),
            installed=False,
            origin="registry",
        ))
    return PluginsResponse(plugins=items, total=len(items))


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
            "source": cfg.source,
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

    def entry_of(info) -> dict:
        entry = info.to_dict()
        entry["compatible"] = getattr(info, "compatible", True)
        entry["capabilities"] = list(getattr(info, "capabilities", []) or [])
        return entry

    bundled = [entry_of(info) for info in manager.list_bundled()]
    installed = [entry_of(info) for info in manager.list_installed()]
    # Three different numbers that all matter: what this build ships, what the
    # user added, and what the registry could install. Reporting only the second
    # reads as "no plugins exist" on a fresh install.
    registry = _load_json("plugins.json")
    available = registry.get("plugins") or []
    return {
        "installed": installed,
        "bundled": bundled,
        "count": len(installed),
        "bundledCount": len(bundled),
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
    for entry in plugins["installed"] + plugins.get("bundled", []):
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
