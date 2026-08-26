"""User / project / global settings store.

Persists the Settings drawer fields (Voice / MCP / Cloud / Metrics
+ Coder sub-mode) in three layers:

  - **In-memory cache** (this module) for the live process
  - **JSON file on disk** at ``<data_dir>/settings.json`` so the
    server can be restarted without losing the user's knobs
  - **Per-project overrides** stored on ``Project.metadata`` for
    things that need to follow a project (e.g. Coder mode)

The frontend talks to ``/api/settings`` (global) and
``/api/projects/{id}/settings`` (per-project). This module owns
the file I/O and the in-memory mirror.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default settings (mirrors web/src/stores/settingsStore.ts)
# ---------------------------------------------------------------------------


@dataclass
class VoiceSettings:
    ttsProvider: str = "edge"          # "edge" | "mock"
    ttsVoice: str = "en-US-AriaNeural"
    sttProvider: str = "mock"          # "mock" | "whisper"
    sttLanguage: str = "en"
    autoPlay: bool = False


@dataclass
class McpSettings:
    enabledServers: list = field(default_factory=lambda: ["filesystem"])
    permissionPrompt: bool = True


@dataclass
class CloudSettings:
    s3Bucket: str = ""
    s3Region: str = "us-east-1"
    s3Endpoint: str = ""
    addressingStyle: str = "auto"     # auto | virtual | path


@dataclass
class MetricsSettings:
    showInFooter: bool = True


@dataclass
class Settings:
    """The full settings blob. Mirrors the frontend store."""
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    cloud: CloudSettings = field(default_factory=CloudSettings)
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    ollama_base_url: str = ""          # http://127.0.0.1:11434 by default
    # New: free-form provider config keyed by model name. Used by
    # the cost / provider system to look up the API key env var.
    provider_env_map: Dict[str, str] = field(default_factory=dict)
    # Round 8: active LLM provider. "openai" | "anthropic" | "ollama"
    # | "deepseek" | "custom" — drives the model router.
    active_provider: str = "openai"
    # Round 8: provider panel — exposed to the frontend as a nested
    # ``provider`` object on the wire (matches
    # web/src/stores/settingsStore.ts:ProviderSettings). Stored flat
    # on disk for backwards-compat with earlier settings.json files.
    provider_ollama_base_url: str = "http://127.0.0.1:11434"
    provider_ollama_model: str = "qwen2.5-coder:7b"
    provider_api_key_env: str = "OPENAI_API_KEY"
    # Round 8: per-tier memory scope. "user" memories follow
    # the user across projects; "project" stay with one project;
    # "session" are kept in-memory only for the current loop.
    memory_user_path: str = ""        # file path; "" → default
    memory_project_path: str = ""     # file path; "" → default


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SettingsStore:
    """Thread-safe settings with disk persistence."""

    def __init__(self, path: Optional[Path] = None) -> None:
        # Default location: <data_dir>/settings.json (or CWD fallback)
        if path is None:
            data_dir = Path(os.environ.get("KAIROS_DATA_DIR", "."))
            path = data_dir / "settings.json"
        self.path = path
        self._lock = threading.Lock()
        self._settings = Settings()
        self._load()

    # -- file I/O -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("settings: failed to load %s: %s", self.path, exc)
            return
        self._settings = _from_dict(raw)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(_to_dict(self._settings), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("settings: failed to save %s: %s", self.path, exc)

    # -- public ---------------------------------------------------------

    def get(self) -> Settings:
        with self._lock:
            return _clone(self._settings)

    def update(self, patch: Dict[str, Any]) -> Settings:
        """Merge *patch* (partial dict) into the current settings.

        The patch can include any of the top-level sections
        (``voice``, ``mcp``, ``cloud``, ``metrics``,
        ``ollama_base_url``, ``provider_env_map``,
        ``active_provider``, ``provider``). Sections not
        in the patch are left untouched. Within a section, only
        the keys present in the patch are updated.

        The ``provider`` section is accepted as a nested dict
        (matching the frontend ``ProviderSettings``) and split into
        the flat ``active_provider`` / ``provider_ollama_base_url``
        / ``provider_ollama_model`` / ``provider_api_key_env``
        fields on disk. For backwards compatibility ``provider``
        keys are also accepted in flat form.
        """
        with self._lock:
            current = _to_dict(self._settings)
            for section, sub in patch.items():
                if section == "ollama_base_url" or section == "provider_env_map":
                    current[section] = sub
                    continue
                if section == "active_provider":
                    current["active_provider"] = sub
                    # Keep nested provider.active in sync so the
                    # SettingsDrawer reads the new value back.
                    if isinstance(current.get("provider"), dict):
                        current["provider"] = dict(current["provider"], active=sub)
                    continue
                if section == "provider" and isinstance(sub, dict):
                    # Nested provider panel from the SettingsDrawer.
                    # We keep both the flat and nested forms in sync so
                    # ``_from_dict(current)`` re-reads the new values
                    # regardless of which shape wins.
                    nested_now = (
                        current.get("provider") if isinstance(current.get("provider"), dict) else {}
                    )
                    flat_active = current.get("active_provider", "openai")
                    flat_url = current.get("provider_ollama_base_url", "http://127.0.0.1:11434")
                    flat_model = current.get("provider_ollama_model", "qwen2.5-coder:7b")
                    flat_key = current.get("provider_api_key_env", "OPENAI_API_KEY")
                    if "active" in sub:
                        flat_active = sub["active"]
                    if "ollamaBaseUrl" in sub:
                        flat_url = sub["ollamaBaseUrl"]
                    if "ollamaModel" in sub:
                        flat_model = sub["ollamaModel"]
                    if "apiKeyEnv" in sub:
                        flat_key = sub["apiKeyEnv"]
                    current["active_provider"] = flat_active
                    current["provider_ollama_base_url"] = flat_url
                    current["provider_ollama_model"] = flat_model
                    current["provider_api_key_env"] = flat_key
                    current["provider"] = {
                        "active": flat_active,
                        "ollamaBaseUrl": flat_url,
                        "ollamaModel": flat_model,
                        "apiKeyEnv": flat_key,
                    }
                    # Avoid a stale read from the prior nested block.
                    _ = nested_now
                    continue
                if section not in current or not isinstance(sub, dict):
                    continue
                if not isinstance(current.get(section), dict):
                    current[section] = {}
                current[section].update(sub)
            self._settings = _from_dict(current)
            self._save()
            return _clone(self._settings)

    def reset(self) -> Settings:
        with self._lock:
            self._settings = Settings()
            self._save()
            return _clone(self._settings)


# ---------------------------------------------------------------------------
# (de)serialization
# ---------------------------------------------------------------------------


def _to_dict(s: Settings) -> dict:
    return {
        "voice": asdict(s.voice),
        "mcp": {
            "enabledServers": list(s.mcp.enabledServers),
            "permissionPrompt": s.mcp.permissionPrompt,
        },
        "cloud": asdict(s.cloud),
        "metrics": asdict(s.metrics),
        "ollama_base_url": s.ollama_base_url,
        "provider_env_map": dict(s.provider_env_map),
        "active_provider": s.active_provider,
        # Nested provider panel — mirrors web/src/stores/settingsStore.ts
        # ProviderSettings so the SettingsDrawer can read/write the
        # whole object with one POST.
        "provider": {
            "active": s.active_provider,
            "ollamaBaseUrl": s.provider_ollama_base_url,
            "ollamaModel": s.provider_ollama_model,
            "apiKeyEnv": s.provider_api_key_env,
        },
        "memory_user_path": s.memory_user_path,
        "memory_project_path": s.memory_project_path,
    }


def _from_dict(d: dict) -> Settings:
    voice = VoiceSettings(**(d.get("voice") or {}))
    mcp_raw = d.get("mcp") or {}
    mcp = McpSettings(
        enabledServers=list(mcp_raw.get("enabledServers") or ["filesystem"]),
        permissionPrompt=bool(mcp_raw.get("permissionPrompt", True)),
    )
    cloud = CloudSettings(**(d.get("cloud") or {}))
    metrics = MetricsSettings(**(d.get("metrics") or {}))
    # Provider panel: prefer nested ``provider`` object; fall back to
    # the flat ``active_provider`` field (older settings.json files).
    nested_provider = d.get("provider") or {}
    return Settings(
        voice=voice, mcp=mcp, cloud=cloud, metrics=metrics,
        ollama_base_url=str(d.get("ollama_base_url", "")),
        provider_env_map=dict(d.get("provider_env_map") or {}),
        active_provider=str(
            nested_provider.get("active")
            or d.get("active_provider")
            or "openai"
        ),
        provider_ollama_base_url=str(
            nested_provider.get("ollamaBaseUrl")
            or d.get("provider_ollama_base_url")
            or "http://127.0.0.1:11434"
        ),
        provider_ollama_model=str(
            nested_provider.get("ollamaModel")
            or d.get("provider_ollama_model")
            or "qwen2.5-coder:7b"
        ),
        provider_api_key_env=str(
            nested_provider.get("apiKeyEnv")
            or d.get("provider_api_key_env")
            or "OPENAI_API_KEY"
        ),
        memory_user_path=str(d.get("memory_user_path") or ""),
        memory_project_path=str(d.get("memory_project_path") or ""),
    )


def _clone(s: Settings) -> Settings:
    return _from_dict(_to_dict(s))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_store: Optional[SettingsStore] = None
_store_lock = threading.Lock()


def get_store() -> SettingsStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SettingsStore()
    return _store


def reset_store() -> None:
    global _store
    _store = None
