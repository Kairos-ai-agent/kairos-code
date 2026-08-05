"""Shared dependencies for the API layer."""

import json
from pathlib import Path

from kairos.config.settings import settings
from kairos.llm.model_router import ModelRouter
from kairos.core.orchestrator import Orchestrator
from kairos.review.engine import ReviewEngine
from kairos.llm.base import LLMConfig

# Initialize core components (singleton pattern)
config_path = Path(__file__).parent.parent / "kairos" / "config" / "models_config.yaml"
model_router = ModelRouter(config_path=config_path)
orchestrator = Orchestrator(model_router=model_router)


def get_review_engine() -> ReviewEngine:
    """Create a review engine with configured LLM.

    Tries custom models from data/settings.json first (user's actual config),
    then falls back to env-based default provider.
    """
    settings_file = Path("./data/settings.json")
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
            for m in s.get("custom_models", []):
                if m.get("api_key"):
                    return ReviewEngine(LLMConfig(
                        provider="anthropic" if m.get("protocol") == "anthropic" else "openai",
                        model=m["model"],
                        api_key=m["api_key"],
                        base_url=m.get("base_url"),
                        temperature=0.3,
                    ))
        except Exception:
            import logging
            logging.getLogger(__name__).debug("Failed to load custom models from settings.json", exc_info=True)
    # Fallback to env-based settings
    provider_config = getattr(settings, settings.default_provider, settings.openai)
    llm_config = LLMConfig(
        provider=settings.default_provider,
        model=provider_config.model,
        api_key=provider_config.api_key,
        base_url=provider_config.base_url,
        temperature=0.3,
    )
    return ReviewEngine(llm_config)
