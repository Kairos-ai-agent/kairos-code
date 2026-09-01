"""Model Router - assigns different LLM models to different agents."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import yaml
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

from kairos.llm.base import BaseLLMProvider, LLMConfig
from kairos.llm.provider_registry import create_provider
from kairos.llm.resilient import wrap_with_resilience

SETTINGS_FILE = (
    Path(os.environ.get("KAIROS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
    / "settings.json"
)


def settings_path() -> Path:
    """Resolve the live settings.json path on every call (not at import
    time). This matters for tests that monkeypatch ``KAIROS_DATA_DIR``
    after the module is already loaded; reading the env at import time
    would freeze the path to the original value.
    """
    return (
        Path(os.environ.get("KAIROS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
        / "settings.json"
    )

class ModelRouter:
    """Routes agents to their assigned LLM models with caching."""

    def __init__(self, config_path: Optional[Path] = None):
        self._role_mapping: Dict[str, str] = {}
        self._model_configs: Dict[str, LLMConfig] = {}
        self._provider_cache: Dict[str, BaseLLMProvider] = {}  # role -> provider
        self._cache_timestamps: Dict[str, float] = {}  # role -> last cache time
        self._cache_ttl = 5.0  # seconds
        self._custom_models_loaded_at: float = 0.0
        self._custom_models_ttl = 5.0  # seconds

        if config_path and config_path.exists():
            self._load_config(config_path)

        # R37+: also load the per-provider openai / anthropic configs
        # eagerly. Previously this was lazy (called on the first
        # get_provider_for_role), which meant a freshly-constructed
        # ModelRouter had no R37+ configs in ``_model_configs`` and
        # tests / other callers couldn't introspect the user's LLM
        # settings. Loading eagerly keeps ``_model_configs`` in sync
        # with what's on disk.
        self._load_custom_models()

        # Load persisted role mappings
        self._load_role_mappings()

    def _load_config(self, path: Path):
        with open(path) as f:
            config = yaml.safe_load(f)
        for name, model_cfg in config.get("models", {}).items():
            self._model_configs[name] = LLMConfig(**model_cfg)
        self._role_mapping = config.get("role_model_mapping", {})

    def _load_custom_models(self):
        """Load custom model configs from settings file.

        R37+: also load the per-provider openai / anthropic configs
        (endpointUrl, baseUrl, apiKey, model) so the user's LLM
        settings in the SettingsDrawer actually drive the Coder /
        Reviewer LLM calls. Without this, the user-set baseUrl /
        apiKey / model were stored in settings.json but never read
        by the model router — the LLM call still used whatever was
        in custom_models / api_keys (legacy fields).
        """
        sf = settings_path()
        if sf.exists():
            try:
                settings = json.loads(sf.read_text(encoding="utf-8"))
                # R37+: openai / anthropic per-provider configs.
                # Prefer the nested ``provider.openai`` shape, fall
                # back to the top-level ``provider_openai``.
                nested = settings.get("provider") or {}
                openai_cfg = (
                    nested.get("openai")
                    if isinstance(nested.get("openai"), dict)
                    else settings.get("provider_openai")
                ) or {}
                anthropic_cfg = (
                    nested.get("anthropic")
                    if isinstance(nested.get("anthropic"), dict)
                    else settings.get("provider_anthropic")
                ) or {}
                active = (nested.get("active")
                          or settings.get("active_provider") or "openai")
                if openai_cfg and (openai_cfg.get("apiKey") or openai_cfg.get("model")):
                    self._model_configs["__r37_openai__"] = LLMConfig(
                        provider="openai",
                        model=openai_cfg.get("model") or "gpt-4o",
                        api_key=openai_cfg.get("apiKey") or "",
                        base_url=openai_cfg.get("baseUrl") or "https://api.openai.com/v1",
                    )
                if anthropic_cfg and (anthropic_cfg.get("apiKey") or anthropic_cfg.get("model")):
                    self._model_configs["__r37_anthropic__"] = LLMConfig(
                        provider="anthropic",
                        model=anthropic_cfg.get("model") or "claude-3-5-sonnet-latest",
                        api_key=anthropic_cfg.get("apiKey") or "",
                        base_url=anthropic_cfg.get("baseUrl") or "https://api.anthropic.com",
                    )
                # R37+: the active provider determines which LLMConfig
                # the Coder / Reviewer actually use. We register both
                # above and pick one based on ``active``.
                if active == "openai" and "__r37_openai__" in self._model_configs:
                    self._model_configs["__active__"] = self._model_configs["__r37_openai__"]
                elif active == "anthropic" and "__r37_anthropic__" in self._model_configs:
                    self._model_configs["__active__"] = self._model_configs["__r37_anthropic__"]
                # Legacy: custom_models + api_keys (R8 shape) still work.
                custom_models = settings.get("custom_models", [])
                for m in custom_models:
                    key = f"custom:{m['name']}"
                    self._model_configs[key] = LLMConfig(
                        provider="openai" if m.get("protocol") == "openai" else "anthropic",
                        model=m["model"],
                        api_key=m.get("api_key", ""),
                        base_url=m.get("base_url"),
                    )
                api_keys = settings.get("api_keys", {})
                if api_keys.get("deepseek"):
                    self._model_configs["deepseek:deepseek-chat"] = LLMConfig(
                        provider="openai", model="deepseek-chat",
                        api_key=api_keys["deepseek"], base_url="https://api.deepseek.com/v1",
                    )
                    self._model_configs["deepseek:deepseek-reasoner"] = LLMConfig(
                        provider="openai", model="deepseek-reasoner",
                        api_key=api_keys["deepseek"], base_url="https://api.deepseek.com/v1",
                    )
            except (OSError, json.JSONDecodeError, KeyError):
                logger.debug("Failed to load custom models from settings.json", exc_info=True)

    def assign_role_model(self, role: str, model_name: str):
        self._role_mapping[role] = model_name
        # Clear cache for this role
        if role in self._provider_cache:
            old = self._provider_cache.pop(role)
            self._cache_timestamps.pop(role, None)
            # Schedule close if event loop is running, otherwise skip
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(old.close())
            except RuntimeError:
                # No event loop running (sync context), skip close
                pass
        # Persist to settings file
        self._save_role_mappings()

    def _save_role_mappings(self):
        sf = settings_path()
        if not sf.exists():
            sf.parent.mkdir(parents=True, exist_ok=True)
            settings = {}
        else:
            try:
                settings = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                settings = {}
        settings["role_mappings"] = dict(self._role_mapping)
        try:
            sf.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            import logging
            logging.getLogger(__name__).debug("Failed to save role mappings", exc_info=True)

    def _load_role_mappings(self):
        sf = settings_path()
        if sf.exists():
            try:
                settings = json.loads(sf.read_text(encoding="utf-8"))
                saved = settings.get("role_mappings", {})
                if saved:
                    self._role_mapping.update(saved)
            except Exception:
                import logging
                logging.getLogger(__name__).debug("Failed to load role mappings", exc_info=True)

    def get_provider_for_role(self, role: str) -> BaseLLMProvider:
        """Get an LLM provider for a specific role, with caching.

        For role lookups, falls back to "default" if the role isn't
        explicitly mapped. For per-task tier lookups (fast / default /
        strong), use ``get_provider_for_task(tier)`` instead.
        """
        return self._build_provider(role, role_mapping_key=role,
                                     default_model_key="default")

    def get_provider_for_task(self, tier: str) -> BaseLLMProvider:
        """Get a provider for a per-task complexity tier.

        R38.6.4: three tiers — "fast" / "default" / "strong". The
        mapping is read from data/settings.json under
        ``role_mappings[task:fast]``, ``role_mappings[task:default]``,
        ``role_mappings[task:strong]``. Falls back to the active
        provider for "default" and to sensible cheap / strong
        models for the extremes if no explicit mapping exists.

        Why three tiers:
        - fast: file reads, status checks, simple Q&A — saves cost
        - default: normal code edits, plan/execute loops
        - strong: complex refactors, hard bug hunts, planning
        """
        tier_key = f"task:{tier}"
        return self._build_provider(tier_key, role_mapping_key=tier_key,
                                     default_model_key=tier)

    def list_role_mappings(self) -> Dict[str, str]:
        return dict(self._role_mapping)

    def list_tier_mappings(self) -> Dict[str, str]:
        """Return only the task:<tier> → model mappings for the UI
        (SettingsDrawer) to display and edit."""
        return {k.removeprefix("task:"): v
                for k, v in self._role_mapping.items()
                if k.startswith("task:")}

    def _build_provider(self, cache_key: str, role_mapping_key: str,
                          default_model_key: str) -> BaseLLMProvider:
        """Shared provider-build path. Cached by ``cache_key``."""
        model_name = self._role_mapping.get(role_mapping_key, default_model_key)

        # Check cache
        now = time.time()
        if cache_key in self._provider_cache:
            cached_time = self._cache_timestamps.get(cache_key, 0)
            if now - cached_time < self._cache_ttl:
                return self._provider_cache[cache_key]

        # Load custom models with TTL (avoid disk I/O on every call)
        if now - self._custom_models_loaded_at > self._custom_models_ttl:
            self._load_custom_models()
            self._custom_models_loaded_at = now

        # Create provider
        config = self._model_configs.get(model_name)
        if config is None:
            config = self._create_dynamic_config(model_name)
        if config is None:
            config = self._model_configs.get("default", LLMConfig(
                provider="openai", model="gpt-4o", api_key="sk-placeholder",
            ))

        raw_provider = create_provider(config)

        # Wrap with retry + exponential backoff + provider failover.
        failover = None
        failover_model = self._role_mapping.get(role_mapping_key + ":failover")
        if failover_model:
            try:
                fc = self._model_configs.get(failover_model)
                if fc is None:
                    fc = self._create_dynamic_config(failover_model)
                if fc is not None:
                    failover = create_provider(fc)
            except Exception:
                logger.debug("failed to build failover provider", exc_info=True)
        provider = wrap_with_resilience(raw_provider, failover=failover)

        # Cache it
        self._provider_cache[cache_key] = provider
        self._cache_timestamps[cache_key] = now

        return provider

    def _create_dynamic_config(self, model_name: str) -> Optional[LLMConfig]:
        sf = settings_path()
        if not sf.exists():
            return None
        try:
            settings = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            import logging
            logging.getLogger(__name__).debug("Failed to read settings for dynamic config", exc_info=True)
            return None

        api_keys = settings.get("api_keys", {})

        if model_name.startswith("deepseek:"):
            actual_model = model_name.split(":", 1)[1]
            api_key = api_keys.get("deepseek", "")
            return LLMConfig(
                provider="openai", model=actual_model,
                api_key=api_key or "sk-placeholder",
                base_url="https://api.deepseek.com/v1",
            )

        if model_name.startswith("custom:"):
            custom_name = model_name.split(":", 1)[1]
            for m in settings.get("custom_models", []):
                if m["name"] == custom_name:
                    return LLMConfig(
                        provider="openai" if m.get("protocol") == "openai" else "anthropic",
                        model=m["model"], api_key=m.get("api_key", ""),
                        base_url=m.get("base_url"),
                    )

        if model_name.startswith("ollama:"):
            # Round-7: local Ollama. Read the model name + base URL
            # from settings (written by providers.integration).
            model = model_name.split(":", 1)[1]
            base_url = settings.get("ollama_base_url", "http://127.0.0.1:11434")
            return LLMConfig(
                provider="ollama", model=model,
                api_key="", base_url=base_url,
            )

        return None

    def get_provider_by_name(self, model_name: str) -> BaseLLMProvider:
        self._load_custom_models()
        config = self._model_configs.get(model_name)
        if config is None:
            raise ValueError(f"No model config found: {model_name}")
        return create_provider(config)

    def list_models(self) -> list[str]:
        self._load_custom_models()
        return list(self._model_configs.keys())

    def list_models_with_info(self) -> list[dict]:
        """Return models with human-readable labels."""
        self._load_custom_models()
        result = []
        LABELS = {
            "default": "Default (GPT-4o)",
            "creative": "Creative (GPT-4o, high temp)",
            "precise": "Precise (Claude Sonnet)",
            "fast": "Fast (DeepSeek Chat)",
            "local": "Local (Ollama Llama3)",
        }
        for key, cfg in self._model_configs.items():
            result.append({
                "id": key,
                "label": LABELS.get(key, key),
                "model": cfg.model,
                "provider": cfg.provider,
            })
        return result
