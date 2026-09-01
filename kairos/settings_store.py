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
class OpenAIProviderConfig:
    """R37+: per-provider config (OpenAI-compatible).

    Mirrors ``web/src/stores/settingsStore.ts:OpenAIConfig`` so the
    frontend's POST /api/projects/settings is round-tripped to disk
    without losing data. ``endpoint_url`` is the full URL the test
    probe hits (R38.5 — the user owns the path); ``base_url`` is
    used by the orchestrator's actual chat calls.
    """
    endpointUrl: str = "https://api.openai.com/v1/chat/completions"
    baseUrl: str = "https://api.openai.com/v1"
    apiKey: str = ""
    model: str = "gpt-4o"


@dataclass
class AnthropicProviderConfig:
    """R37+: per-provider config (Anthropic-compatible)."""
    endpointUrl: str = "https://api.anthropic.com/v1/messages"
    baseUrl: str = "https://api.anthropic.com"
    apiKey: str = ""
    model: str = "claude-3-5-sonnet-latest"


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
    # R37+: per-provider (openai / anthropic) full configs. Stored
    # nested in the wire ``provider`` object so the frontend can
    # round-trip everything in one POST. Pre-existing flat fields
    # above are kept for backwards-compat with older settings.json.
    provider_openai: OpenAIProviderConfig = field(
        default_factory=OpenAIProviderConfig)
    provider_anthropic: AnthropicProviderConfig = field(
        default_factory=AnthropicProviderConfig)
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
        # Default location: <repo>/data/settings.json — the SAME file
        # the model router reads (kairos/llm/model_router.py
        # settings_path()). Previously this defaulted to CWD-relative
        # "./settings.json", so the SettingsDrawer's saved provider
        # config landed in a different file depending on how the
        # backend was launched — after a restart (different CWD) the
        # saved LLM settings were silently gone. Anchoring to the
        # repo-absolute data dir makes persistence survive ANY launch
        # method, and puts the drawer's provider config in the file
        # the model router actually reads. KAIROS_DATA_DIR still
        # overrides it.
        if path is None:
            data_dir = Path(os.environ.get(
                "KAIROS_DATA_DIR",
                Path(__file__).resolve().parent.parent / "data",
            ))
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
            # Merge, don't replace: data/settings.json also holds
            # legacy keys owned by other subsystems (api_keys,
            # custom_models, role_mappings, loop_config). A plain
            # replace would silently wipe the user's custom models /
            # role assignments on every SettingsDrawer save.
            existing: Dict[str, Any] = {}
            if self.path.is_file():
                try:
                    existing = json.loads(self.path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = {}
            merged = {**existing, **_to_dict(self._settings)}
            self.path.write_text(
                json.dumps(merged, indent=2, ensure_ascii=False),
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
        ``active_provider``, ``provider``,
        ``provider_openai``, ``provider_anthropic``).
        Sections not in the patch are left untouched. Within a
        section, only the keys present in the patch are updated.

        The ``provider`` section is accepted as a nested dict
        (matching the frontend ``ProviderSettings``) and split into
        the flat ``active_provider`` / ``provider_ollama_base_url``
        / ``provider_ollama_model`` / ``provider_api_key_env``
        fields on disk. R37+ also persists the per-provider openai /
        anthropic configs (endpointUrl, baseUrl, apiKey, model) so
        the user's LLM settings survive a backend restart.
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
                    # R37+: persist the per-provider openai / anthropic
                    # configs (endpointUrl, baseUrl, apiKey, model).
                    # Without this, the user's LLM settings were lost
                    # on backend restart (the new keys were silently
                    # dropped by the older update() code).
                    if isinstance(sub.get("openai"), dict):
                        current["provider_openai"] = dict(
                            current.get("provider_openai") or {}, **sub["openai"])
                        if isinstance(current.get("provider"), dict):
                            current["provider"] = dict(
                                current["provider"],
                                openai=current["provider_openai"])
                    if isinstance(sub.get("anthropic"), dict):
                        current["provider_anthropic"] = dict(
                            current.get("provider_anthropic") or {}, **sub["anthropic"])
                        if isinstance(current.get("provider"), dict):
                            current["provider"] = dict(
                                current["provider"],
                                anthropic=current["provider_anthropic"])
                    # Avoid a stale read from the prior nested block.
                    _ = nested_now
                    continue
                if section == "provider_openai" and isinstance(sub, dict):
                    # Top-level provider_openai (alternative to nested).
                    current["provider_openai"] = dict(
                        current.get("provider_openai") or {}, **sub)
                    continue
                if section == "provider_anthropic" and isinstance(sub, dict):
                    # Top-level provider_anthropic (alternative to nested).
                    current["provider_anthropic"] = dict(
                        current.get("provider_anthropic") or {}, **sub)
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
        # whole object with one POST. R37+ includes the full openai /
        # anthropic per-provider configs (endpointUrl, baseUrl, apiKey,
        # model). The legacy ollama fields stay for backwards-compat.
        "provider": {
            "active": s.active_provider,
            "ollamaBaseUrl": s.provider_ollama_base_url,
            "ollamaModel": s.provider_ollama_model,
            "apiKeyEnv": s.provider_api_key_env,
            "openai": asdict(s.provider_openai),
            "anthropic": asdict(s.provider_anthropic),
        },
        # R37+: also keep the per-provider configs at the top level so
        # older readers (and the migration code) can find them.
        "provider_openai": asdict(s.provider_openai),
        "provider_anthropic": asdict(s.provider_anthropic),
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
    # R37+: openai / anthropic per-provider configs. Prefer the nested
    # ``provider.openai`` object (the new wire format), fall back to
    # the top-level ``provider_openai`` (some legacy code paths), and
    # finally to defaults.
    openai_raw = (
        nested_provider.get("openai")
        if isinstance(nested_provider.get("openai"), dict)
        else (d.get("provider_openai") or {})
    )
    anthropic_raw = (
        nested_provider.get("anthropic")
        if isinstance(nested_provider.get("anthropic"), dict)
        else (d.get("provider_anthropic") or {})
    )
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
        provider_openai=OpenAIProviderConfig(**openai_raw),
        provider_anthropic=AnthropicProviderConfig(**anthropic_raw),
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
