"""Project manifest loader.

Mirrors the cloud task Harness's `Manifest` pattern: a per-project YAML file
that declares workspace structure, trust boundaries, sandbox limits,
and agent settings. Kairos looks for `<work_dir>/.kairos/manifest.yaml`
at agent creation time and uses it to:

  - Pre-seed TerminalTool / file tools with deny paths
  - Pick the specialist reviewer set
  - Bound concurrency

The file is OPTIONAL. When it's missing, every field falls back to a
sane default. When it's malformed, we log a warning and use the
defaults rather than blowing up agent creation.

This is intentionally a single small module — the loader is the only
hot spot. Configuration *consumers* (Orchestrator) call it once when
they set up the project.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_PATH = ".kairos/manifest.yaml"

# Defaults used when the manifest is missing or a specific field is
# absent. They're chosen to match the long-standing behaviour of the
# Orchestrator before the manifest existed, so existing projects keep
# working unchanged.
DEFAULTS: Dict[str, Any] = {
    "workspace": {
        "name": None,
        "type": "python",
        "entry": None,
    },
    "trust": {
        "paths": ["src/**", "tests/**", "lib/**"],
        "deny": [".env", "secrets/**", "**/*.key", "**/*.pem"],
    },
    "sandbox": {
        "network": False,
        "memory_mb": 0,        # 0 = no cap
        "cpu": 0.0,            # 0 = no cap
        "timeout_s": 300,
    },
    "agents": {
        "max_concurrent": 2,
        "max_tool_turns": 15,
    },
    "reviewers": {
        # the cloud task-style: "reviewer" is the main + specialists are extra.
        "enabled": ["reviewer", "security_reviewer", "perf_reviewer"],
        "weights": {
            "reviewer": 0.5,
            "security_reviewer": 0.3,
            "perf_reviewer": 0.2,
        },
    },
    "model_router": {
        # Map role -> model name. Empty = use what's already in
        # settings.json.
        "role_models": {},
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Override `base` with `override` recursively. Lists replace, not extend."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class WorkspaceSpec:
    name: Optional[str] = None
    type: str = "python"
    entry: Optional[str] = None


@dataclass
class TrustSpec:
    paths: List[str] = field(default_factory=lambda: list(DEFAULTS["trust"]["paths"]))
    deny: List[str] = field(default_factory=lambda: list(DEFAULTS["trust"]["deny"]))


@dataclass
class SandboxSpec:
    network: bool = False
    memory_mb: int = 0
    cpu: float = 0.0
    timeout_s: int = 300


@dataclass
class AgentsSpec:
    max_concurrent: int = 2
    max_tool_turns: int = 15


@dataclass
class ReviewersSpec:
    enabled: List[str] = field(default_factory=lambda: list(DEFAULTS["reviewers"]["enabled"]))
    weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULTS["reviewers"]["weights"])
    )


@dataclass
class ModelRouterSpec:
    role_models: Dict[str, str] = field(default_factory=dict)


@dataclass
class Manifest:
    """Loaded manifest. `source_path` is None if defaults were used."""
    workspace: WorkspaceSpec
    trust: TrustSpec
    sandbox: SandboxSpec
    agents: AgentsSpec
    reviewers: ReviewersSpec
    model_router: ModelRouterSpec
    source_path: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": {
                "name": self.workspace.name,
                "type": self.workspace.type,
                "entry": self.workspace.entry,
            },
            "trust": {
                "paths": list(self.trust.paths),
                "deny": list(self.trust.deny),
            },
            "sandbox": {
                "network": self.sandbox.network,
                "memory_mb": self.sandbox.memory_mb,
                "cpu": self.sandbox.cpu,
                "timeout_s": self.sandbox.timeout_s,
            },
            "agents": {
                "max_concurrent": self.agents.max_concurrent,
                "max_tool_turns": self.agents.max_tool_turns,
            },
            "reviewers": {
                "enabled": list(self.reviewers.enabled),
                "weights": dict(self.reviewers.weights),
            },
            "model_router": {
                "role_models": dict(self.model_router.role_models),
            },
        }


def _build(raw: Dict[str, Any], source: Optional[Path]) -> Manifest:
    """Turn a merged dict into a typed Manifest."""
    return Manifest(
        workspace=WorkspaceSpec(**raw["workspace"]),
        trust=TrustSpec(**raw["trust"]),
        sandbox=SandboxSpec(**raw["sandbox"]),
        agents=AgentsSpec(**raw["agents"]),
        reviewers=ReviewersSpec(**raw["reviewers"]),
        model_router=ModelRouterSpec(**raw["model_router"]),
        source_path=source,
    )


def _default() -> Manifest:
    """Return a Manifest populated entirely from DEFAULTS."""
    return _build(_deep_merge(DEFAULTS, {}), source=None)


def load(project_dir: Optional[Path] = None,
         manifest_path: Optional[Path] = None) -> Manifest:
    """Load the manifest for `project_dir`, or `manifest_path` directly.

    Resolution order:
        1. Explicit `manifest_path` (test override)
        2. `<project_dir>/.kairos/manifest.yaml`
        3. Defaults (no source_path)
    """
    target: Optional[Path] = None
    if manifest_path is not None:
        target = Path(manifest_path)
    elif project_dir is not None:
        candidate = Path(project_dir) / DEFAULT_MANIFEST_PATH
        if candidate.exists() and candidate.is_file():
            target = candidate
    if target is None:
        return _default()
    try:
        raw_text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("manifest: failed to read %s: %s; using defaults",
                       target, exc)
        return _default()
    try:
        user_raw = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        logger.warning(
            "manifest: %s is not valid YAML: %s; using defaults",
            target, exc,
        )
        return _default()
    if not isinstance(user_raw, dict):
        logger.warning(
            "manifest: %s root must be a mapping; using defaults", target
        )
        return _default()
    merged = _deep_merge(DEFAULTS, user_raw)
    return _build(merged, source=target)


def render_template() -> str:
    """A starter manifest with all fields shown. Useful for
    `kairos init` or `cp .kairos/manifest.yaml.example manifest.yaml`."""
    return """\
# Kairos project manifest
# All fields are optional. Anything you omit falls back to the built-in
# defaults. Edit this file and restart the agent loop to apply.

workspace:
  # name: my-app
  # type: python            # python | node | go | rust | generic
  # entry: src/main.py

trust:
  # Globs that agents are allowed to read/write.
  paths:
    - src/**
    - tests/**
    - lib/**
  # Globs that are forbidden even inside work_dir.
  deny:
    - .env
    - secrets/**
    - "**/*.key"
    - "**/*.pem"

sandbox:
  network: false
  memory_mb: 0            # 0 = no cap
  cpu: 0.0                # 0.0 = no cap
  timeout_s: 300

agents:
  max_concurrent: 2
  max_tool_turns: 15

reviewers:
  # Which reviewer roles participate. "reviewer" is the main one; the
  # _reviewer suffix variants are specialists whose scores are weighted
  # against the main one.
  enabled:
    - reviewer
    - security_reviewer
    - perf_reviewer
  weights:
    reviewer: 0.5
    security_reviewer: 0.3
    perf_reviewer: 0.2

model_router:
  # Per-role model override for THIS project only. Empty = inherit
  # from data/settings.json. Example:
  # role_models:
  #   coder: creative
  #   reviewer: precise
  role_models: {}
"""
