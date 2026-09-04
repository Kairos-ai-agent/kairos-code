"""LLM Provider Registry - manages and creates LLM providers."""

from __future__ import annotations

from typing import Dict, Type

from kairos.llm.base import BaseLLMProvider, LLMConfig

class ProviderRegistry:
    """Registry for LLM provider implementations."""

    _providers: Dict[str, Type[BaseLLMProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_class: Type[BaseLLMProvider]):
        """Register a provider class for a given name."""
        cls._providers[name] = provider_class

    @classmethod
    def get(cls, name: str) -> Type[BaseLLMProvider] | None:
        """Get a registered provider class by name."""
        return cls._providers.get(name)

    @classmethod
    def list_providers(cls) -> list[str]:
        """List all registered provider names."""
        return list(cls._providers.keys())

def create_provider(config: LLMConfig) -> BaseLLMProvider:
    """Create an LLM provider instance from config.

    Unknown provider names fall back to the OpenAI-compatible provider
    (most "unknown" providers are really OpenAI-compatible anyway), so a
    bad / foreign model config can never make agent wiring fail. Without
    this, an unregistered provider name raised ValueError inside
    ``get_provider_for_role``, which left ``project.coder`` as None and
    broke every chat call with "No Coder agent wired" (503). Now the
    agent still gets wired and the error surfaces at call time instead.
    """
    provider_class = ProviderRegistry.get(config.provider)
    if provider_class is None:
        provider_class = ProviderRegistry.get("openai")
        if provider_class is None:
            raise ValueError(
                f"No LLM provider registered; {config.provider} is "
                f"unknown and the openai fallback is missing."
            )
    return provider_class(config)

# Auto-import providers to register them
def _register_all():
    from kairos.llm.providers import openai_provider  # noqa: F401
    from kairos.llm.providers import anthropic_provider  # noqa: F401
    from kairos.llm.providers import deepseek_provider  # noqa: F401
    from kairos.llm.providers import ollama_provider  # noqa: F401

_register_all()
