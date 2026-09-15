"""Plugin system.

A plugin is a directory you can drop into ``~/.kairos/plugins/`` (or
point ``kairos-plugin.yaml`` at via ``--plugin-dir``) that bundles
one or more of:

  - ``skills/``     — additional ``.md`` skill files (YAML frontmatter)
  - ``agents/``     — additional role YAMLs (system_prompt + tool list)
  - ``hooks/hooks.json`` — lifecycle hook subscriptions
  - ``mcp.yaml``    — MCP server config (merged with the user's)
  - ``commands/``   — slash commands the CLI exposes

Mirrors the agentic CLI's plugin model (and the cloud task's plugins/ subdir).
The intent: a team can package "everything we need to use Kairos on
our monorepo" as a single directory that they check into git, and
each developer just runs ``kairos plugin install <path>``.

We provide:

  - ``PluginManager.install(source)``      — copy a plugin into the
    user-scope plugins dir
  - ``PluginManager.uninstall(name)``      — remove by name
  - ``PluginManager.list_installed()``     — enumerate
  - ``PluginManager.load_all()``          — merge all of the above
    into the active Kairos runtime

Loading is intentionally incremental — we only merge files that
exist. Missing pieces are no-ops so a plugin can ship a partial
overlay (e.g. just skills, no agents).
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from kairos.config.merge import deep_merge

logger = logging.getLogger(__name__)


PLUGIN_MANIFEST = "kairos-plugin.yaml"


@dataclass
class PluginInfo:
    """Metadata about an installed plugin."""
    name: str
    version: str = "0.0.0"
    description: str = ""
    path: Path = field(default_factory=Path)
    enabled: bool = True
    #: R38.11: what the plugin contributes, declared in its manifest
    #: (``capabilities: [skills, hooks, mcp]``). Empty means "not declared" —
    #: the file layout still tells the truth, so nothing is lost.
    capabilities: List[str] = field(default_factory=list)
    #: A version range like ``">=0.1,<0.2"``. Empty means "no constraint".
    compatible: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "path": str(self.path),
            "enabled": self.enabled,
            "capabilities": list(self.capabilities),
            "compatible": self.compatible,
        }


#: The surfaces a plugin may contribute to. Mirrors what ``load_all`` detects.
KNOWN_CAPABILITIES: Tuple[str, ...] = ("skills", "agents", "hooks", "mcp",
                                       "commands")


def check_compatible(compatible: str,
                     version: Optional[str] = None) -> Tuple[bool, str]:
    """Whether a plugin's ``compatible`` range admits this Kairos build.

    Accepts ``>=``, ``>``, ``<=``, ``<`` and ``=`` against dotted versions,
    comma-separated: ``">=0.1,<0.2"``. An empty range, or one we cannot parse,
    counts as compatible — a plugin author's typo must not silently disable
    their plugin, but a real mismatch must not be hidden either, so the caller
    gets the reason back and decides.
    """
    spec = (compatible or "").strip()
    if not spec:
        return True, "no constraint"
    from kairos.updater import version_tuple
    if version is None:
        from kairos import __version__ as version
    current = version_tuple(str(version))

    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        op = "="
        for candidate in (">=", "<=", ">", "<", "="):
            if part.startswith(candidate):
                op, part = candidate, part[len(candidate):].strip()
                break
        if not any(ch.isdigit() for ch in part):
            # "garbage" parses to (0,) and would look like a mismatch. A typo
            # in a manifest must not silently disable the plugin.
            return True, f"unparsed constraint {raw.strip()!r} (ignored)"
        target = version_tuple(part.lstrip("vV"))
        if op == ">=" and not current >= target:
            return False, f"requires {spec}, this build is {version}"
        if op == ">" and not current > target:
            return False, f"requires {spec}, this build is {version}"
        if op == "<=" and not current <= target:
            return False, f"requires {spec}, this build is {version}"
        if op == "<" and not current < target:
            return False, f"requires {spec}, this build is {version}"
        if op == "=" and current != target:
            return False, f"requires {spec}, this build is {version}"
    return True, "ok"


@dataclass
class LoadedPlugin:
    """The actual files a plugin contributes to a runtime."""
    info: PluginInfo
    skills_dir: Optional[Path] = None
    agents_dir: Optional[Path] = None
    hooks_file: Optional[Path] = None
    mcp_file: Optional[Path] = None
    commands_dir: Optional[Path] = None


class PluginError(RuntimeError):
    """Raised when a plugin cannot be installed or loaded."""


class PluginManager:
    """Manages a flat collection of plugins under a single root dir."""

    def __init__(self, plugins_root: Optional[Path] = None):
        self.plugins_root = Path(plugins_root) if plugins_root \
            else (Path.home() / ".kairos" / "plugins")
        self.plugins_root.mkdir(parents=True, exist_ok=True)

    # -- discovery --------------------------------------------------------

    def list_installed(self) -> List[PluginInfo]:
        """Enumerate every plugin in the root."""
        out: List[PluginInfo] = []
        for child in sorted(self.plugins_root.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / PLUGIN_MANIFEST
            info = self._read_manifest(manifest_path, child)
            if info is None:
                continue
            out.append(info)
        return out

    def _read_manifest(
        self, manifest_path: Path, plugin_path: Path
    ) -> Optional[PluginInfo]:
        if not manifest_path.exists():
            return None
        try:
            raw = yaml.safe_load(
                manifest_path.read_text(encoding="utf-8", errors="replace")
            ) or {}
        except yaml.YAMLError as exc:
            logger.warning(
                "plugin: bad manifest at %s: %s; skipping",
                manifest_path, exc,
            )
            return None
        if not isinstance(raw, dict):
            return None
        # The manifest MAY also have a single top-level key equal to
        # the plugin's directory name; that overrides the name.
        name = raw.get("name") or plugin_path.name
        caps = raw.get("capabilities")
        if isinstance(caps, str):
            caps = [c for c in caps.replace(",", " ").split() if c]
        caps = [str(c).strip().lower() for c in (caps or []) if str(c).strip()]
        unknown = [c for c in caps if c not in KNOWN_CAPABILITIES]
        if unknown:
            logger.warning(
                "plugin %s: unknown capabilities %s (known: %s)",
                name, unknown, list(KNOWN_CAPABILITIES))
        return PluginInfo(
            name=name,
            version=str(raw.get("version", "0.0.0")),
            description=str(raw.get("description", "")),
            path=plugin_path,
            enabled=bool(raw.get("enabled", True)),
            capabilities=[c for c in caps if c in KNOWN_CAPABILITIES],
            compatible=str(raw.get("compatible", "") or ""),
        )

    # -- install / uninstall ---------------------------------------------

    def install(self, source: Path) -> PluginInfo:
        """Copy a plugin directory into the root and return its info."""
        source = Path(source).resolve()
        if not source.is_dir():
            raise PluginError(f"plugin source {source} is not a directory")
        manifest = source / PLUGIN_MANIFEST
        info = self._read_manifest(manifest, source)
        if info is None:
            # Allow installation of plugin dirs without a manifest;
            # fall back to the directory name.
            info = PluginInfo(name=source.name, path=source)
        dest = self.plugins_root / info.name
        if dest.exists():
            raise PluginError(
                f"plugin {info.name!r} already installed at {dest}; "
                "uninstall it first"
            )
        try:
            shutil.copytree(source, dest)
        except shutil.Error as exc:
            raise PluginError(f"copy failed: {exc}") from exc
        # Re-read the manifest from the destination so version etc.
        # are picked up correctly.
        new_info = self._read_manifest(dest / PLUGIN_MANIFEST, dest)
        return new_info or PluginInfo(name=info.name, path=dest)

    def uninstall(self, name: str) -> bool:
        """Remove a plugin by name. Returns True if a directory was
        removed, False if no such plugin was installed."""
        target = self.plugins_root / name
        if not target.exists() or not target.is_dir():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return True

    # -- loading ----------------------------------------------------------

    def load_all(self) -> List[LoadedPlugin]:
        """Load every installed plugin (enabled only) and report what
        files each one contributes."""
        loaded: List[LoadedPlugin] = []
        for info in self.list_installed():
            if not info.enabled:
                continue
            def child(name: str) -> Optional[Path]:
                p = info.path / name
                return p if p.exists() and p.is_dir() else None
            def child_file(name: str) -> Optional[Path]:
                p = info.path / name
                return p if p.exists() and p.is_file() else None
            loaded.append(LoadedPlugin(
                info=info,
                skills_dir=child("skills"),
                agents_dir=child("agents"),
                hooks_file=child_file("hooks/hooks.json"),
                mcp_file=child_file("mcp.yaml"),
                commands_dir=child("commands"),
            ))
        return loaded

    # -- aggregate config helpers ---------------------------------------

    @staticmethod
    def aggregate_mcp_configs(plugins: List[LoadedPlugin]) -> Dict[str, Any]:
        """Merge every plugin's ``mcp.yaml`` into one dict.

        The first plugin's servers are overridden by later ones on
        name collision (last-wins), matching the precedence we use
        elsewhere. The caller is expected to feed the result into
        ``McpRegistry.load`` style ingestion.
        """
        out: Dict[str, Any] = {"mcp_servers": {}}
        for p in plugins:
            if not p.mcp_file:
                continue
            try:
                raw = yaml.safe_load(
                    p.mcp_file.read_text(encoding="utf-8", errors="replace")
                ) or {}
            except yaml.YAMLError as exc:
                logger.warning(
                    "plugin: bad mcp.yaml in %s: %s", p.info.name, exc
                )
                continue
            if not isinstance(raw, dict):
                continue
            out = deep_merge(out, raw)
        return out


# ---------------------------------------------------------------------------
# Manifest template
# ---------------------------------------------------------------------------

MANIFEST_TEMPLATE = """\
# Kairos plugin manifest
name: {name}            # required; used as the install dir name
version: 0.1.0
description: >
  One-line description of what this plugin does.

# Optional: bundle assets. Any directory or file that's missing
# is just skipped at load time, so plugins can ship a partial
# overlay (skills only, or just MCP servers, etc.).

# skills_dir: skills/
# agents_dir: agents/
# hooks_file: hooks/hooks.json
# mcp_file:    mcp.yaml
# commands_dir: commands/
"""
