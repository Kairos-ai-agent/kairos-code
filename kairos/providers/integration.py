"""Bridge between the round-7 settings layer and the round-1 ModelRouter.

The :class:`ModelRouter` reads ``<data_dir>/settings.json`` (a
file managed by older code in ``kairos.llm.model_router``). The
round-7 ``SettingsStore`` reads/writes the *same* path so a
single source of truth holds both layers' state.

This module provides the bridge:

  - :func:`set_ollama_provider(base_url, model, role)` — flip
    the active provider for *role* (or both coder + reviewer) to
    Ollama, writing the right keys to settings.json so the next
    :meth:`ModelRouter.get_provider_for_role` call returns an
    :class:`OllamaProvider`.
  - :func:`clear_ollama_provider(role)` — restore the previous
    non-Ollama provider.
  - :func:`current_provider(role)` — return the *resolved* class
    name for a role right now (without instantiating the actual
    client). Useful for the UI to show "Current: ollama
    (qwen2.5-coder:7b)".

The whole point: a user clicks "Use local Ollama" in the
Settings drawer → ``set_ollama_provider()`` writes the config →
``ModelRouter`` builds an :class:`OllamaProvider` → the Coder
agent runs against it. No code changes anywhere else.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _settings_path() -> Path:
    """Return the live settings.json path, reading KAIROS_DATA_DIR
    on every call (not at import time) so tests can flip the env
    between runs.
    """
    base = os.environ.get("KAIROS_DATA_DIR")
    if base:
        return Path(base) / "settings.json"
    # Default: walk up from this file to the repo root and use
    # ``data/settings.json`` (matches ModelRouter's default).
    return Path(__file__).parent.parent.parent / "data" / "settings.json"


def _read_settings() -> dict:
    p = _settings_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("settings: failed to read %s: %s", p, exc)
        return {}


def _write_settings(data: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                 encoding="utf-8")


@dataclass
class ResolvedProvider:
    name: str
    model: str
    base_url: str = ""
    api_key: str = ""


def current_provider(role: str = "coder") -> ResolvedProvider:
    """Return a plain-data view of the *resolved* provider for *role*.

    Doesn't instantiate the actual LLM client. The model_router's
    cache may have a stale entry, so we read the on-disk file
    directly to get the canonical answer.
    """
    settings = _read_settings()
    custom_models = settings.get("custom_models", [])
    role_mappings = settings.get("role_mappings", {})

    model_name = role_mappings.get(role, "default")
    if model_name == "default":
        # default role → look up a "default" mapping; fall back to gpt-4o
        return ResolvedProvider(name="openai", model="gpt-4o")

    # Find a matching custom model
    for m in custom_models:
        key = f"custom:{m.get('name', '')}"
        if key == model_name:
            proto = m.get("protocol", "openai")
            return ResolvedProvider(
                name=proto,
                model=m.get("model", ""),
                base_url=m.get("base_url", ""),
                api_key=m.get("api_key", ""),
            )

    # Or the special "ollama" / "deepseek" entries we manage
    if model_name.startswith("ollama:"):
        model = model_name.split(":", 1)[1]
        return ResolvedProvider(
            name="ollama", model=model,
            base_url=settings.get("ollama_base_url", "http://127.0.0.1:11434"),
        )
    if model_name.startswith("deepseek:"):
        model = model_name.split(":", 1)[1]
        return ResolvedProvider(
            name="deepseek", model=model,
            base_url="https://api.deepseek.com/v1",
        )

    return ResolvedProvider(name="openai", model=model_name)


def set_ollama_provider(
    base_url: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5-coder:7b",
    role: str = "all",
) -> Dict[str, str]:
    """Switch *role* to Ollama. ``role="all"`` flips both coder + reviewer.

    The settings.json layout is::

        ollama_base_url: "<base_url>"
        role_mappings:
          coder: ollama:<model>
          reviewer: ollama:<model>
          (previous values preserved as ":previous" siblings if
           they were non-default)

    Returns a dict with the resulting role → model mapping.
    """
    settings = _read_settings()
    settings["ollama_base_url"] = base_url
    role_mappings = dict(settings.get("role_mappings") or {})

    roles = ["coder", "reviewer"] if role == "all" else [role]
    for r in roles:
        # Remember the previous non-ollama value under :previous
        prev = role_mappings.get(r)
        if prev and not str(prev).startswith("ollama:"):
            role_mappings[f"{r}:previous"] = prev
        role_mappings[r] = f"ollama:{model}"

    settings["role_mappings"] = role_mappings
    _write_settings(settings)
    return {r: role_mappings[r] for r in roles}


def clear_ollama_provider(role: str = "all") -> Dict[str, str]:
    """Restore the previous non-Ollama provider for *role*."""
    settings = _read_settings()
    role_mappings = dict(settings.get("role_mappings") or {})
    roles = ["coder", "reviewer"] if role == "all" else [role]
    for r in roles:
        prev = role_mappings.pop(f"{r}:previous", None)
        if prev:
            role_mappings[r] = prev
        else:
            role_mappings.pop(r, None)
    settings["role_mappings"] = role_mappings
    _write_settings(settings)
    return {r: role_mappings.get(r) for r in roles}


def is_ollama_active(role: str = "coder") -> bool:
    """Return True if *role* is currently mapped to Ollama."""
    settings = _read_settings()
    role_mappings = settings.get("role_mappings") or {}
    return str(role_mappings.get(role, "")).startswith("ollama:")
