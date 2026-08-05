"""Model Router - assigns different LLM models to different agents."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import yaml
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

from kairos.llm.base import BaseLLMProvider, LLMConfig
from kairos.llm.provider_registry import create_provider
from kairos.llm.resilient import wrap_with_resilience

SETTINGS_FILE = Path(__file__).parent.parent.parent / "data" / "settings.json"

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

        # Load persisted role mappings
        self._load_role_mappings()

    def _load_config(self, path: Path):
        with open(path) as f:
            config = yaml.safe_load(f)
        for name, model_cfg in config.get("models", {}).items():
            self._model_configs[name] = LLMConfig(**model_cfg)
        self._role_mapping = config.get("role_model_mapping", {})

    def _load_custom_models(self):
        """Load custom model configs from settings file."""
        if SETTINGS_FILE.exists():
            try:
                settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
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
        if not SETTINGS_FILE.exists():
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            settings = {}
        else:
            try:
                settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                settings = {}
        settings["role_mappings"] = dict(self._role_mapping)
        try:
            SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            import logging
            logging.getLogger(__name__).debug("Failed to save role mappings", exc_info=True)

    def _load_role_mappings(self):
        if SETTINGS_FILE.exists():
            try:
                settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                saved = settings.get("role_mappings", {})
                if saved:
                    self._role_mapping.update(saved)
            except Exception:
                import logging
                logging.getLogger(__name__).debug("Failed to load role mappings", exc_info=True)

    def get_provider_for_role(self, role: str) -> BaseLLMProvider:
        """Get an LLM provider for a specific role, with caching."""
        model_name = self._role_mapping.get(role, "default")

        # Check cache
        now = time.time()
        if role in self._provider_cache:
            cached_time = self._cache_timestamps.get(role, 0)
            if now - cached_time < self._cache_ttl:
                return self._provider_cache[role]

        # Load custom models with TTL (avoid disk I/O on every call)
        now = time.time()
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
        # Failover: if a "failover" model is configured for this role
        # in data/settings.json (role_mappings[role + ":failover"]),
        # the resilient wrapper uses it after 3 consecutive failures.
        failover = None
        failover_model = self._role_mapping.get(role + ":failover")
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
        self._provider_cache[role] = provider
        self._cache_timestamps[role] = now

        return provider

    def _create_dynamic_config(self, model_name: str) -> Optional[LLMConfig]:
        if not SETTINGS_FILE.exists():
            return None
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
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

    def list_role_mappings(self) -> Dict[str, str]:
        return dict(self._role_mapping)
