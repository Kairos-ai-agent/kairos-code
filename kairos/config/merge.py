"""Hierarchical config merging.

Configuration in Kairos is loaded from several scopes, in order
from most-specific to least-specific:

  1. Plugin (highest priority)         <plugins>/<name>/<file>.yaml
  2. Project                          <work_dir>/.kairos/<file>.yaml
  3. User                             ~/.kairos/<file>.yaml
  4. Built-in defaults (lowest)

Mirrors Claude Code's "precedence: enterprise > user > project > plugin"
(inverted here because plugin code is usually the *latest* to add
behavior and should win over a stale user config).

Scope of this module: provide a single ``merge_configs()`` helper
that:

  - takes a list of file paths (highest priority first)
  - reads each as YAML
  - deep-merges them with a configurable strategy
  - returns a single dict that callers can pick fields out of

Files that don't exist are skipped silently. A file with bad YAML
emits a warning and contributes nothing rather than failing the
whole load.

Existing config loaders (mcp_client, manifest, permissions) all
use their own bespoke merge logic. The intent of this module is
to give them a shared, well-tested primitive so we don't keep
re-implementing it.
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

logger = logging.getLogger(__name__)


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `override` into `base` recursively. Lists replace, not extend.

    Returns a new dict; does not mutate either input.
    """
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except yaml.YAMLError as exc:
        logger.warning("config.merge: bad YAML in %s: %s; skipping", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def merge_configs(
    paths: Sequence[Path],
    defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Load each YAML file in `paths` (highest priority first) and
    deep-merge them all together with `defaults` as the base.

    Order: defaults < paths[0] < paths[1] < ... < paths[-1].
    Later entries override earlier ones.
    """
    out: Dict[str, Any] = dict(defaults) if defaults else {}
    for path in paths:
        out = deep_merge(out, _load_yaml(Path(path)))
    return out


def hierarchical_search_paths(
    project_dir: Optional[Path] = None,
    user_dir: Optional[Path] = None,
    file_name: str = "config.yaml",
    extra_plugin_dirs: Iterable[Path] = (),
) -> List[Path]:
    """Compute the file search order for a given config file.

    Order (lowest priority first):
      1. <user_dir>/<file_name>     (typically ~/.kairos/<file_name>)
      2. <project_dir>/.kairos/<file_name>
      3. <plugin>/<file_name> for each plugin in extra_plugin_dirs

    Callers pass the result to ``merge_configs`` in reverse order
    so plugins win over project wins over user.
    """
    user_dir = Path(user_dir) if user_dir else Path.home() / ".kairos"
    paths: List[Path] = []
    if user_dir:
        paths.append(user_dir / file_name)
    if project_dir:
        paths.append(Path(project_dir) / ".kairos" / file_name)
    for p in extra_plugin_dirs:
        paths.append(Path(p) / file_name)
    return paths
