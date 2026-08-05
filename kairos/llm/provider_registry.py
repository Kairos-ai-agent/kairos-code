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
    """Create an LLM provider instance from config."""
    provider_class = ProviderRegistry.get(config.provider)
    if provider_class is None:
        raise ValueError(
            f"Unknown LLM provider: {config.provider}. "
            f"Available: {ProviderRegistry.list_providers()}"
        )
    return provider_class(config)

# Auto-import providers to register them
def _register_all():
    from kairos.llm.providers import openai_provider  # noqa: F401
    from kairos.llm.providers import anthropic_provider  # noqa: F401
    from kairos.llm.providers import deepseek_provider  # noqa: F401
    from kairos.llm.providers import ollama_provider  # noqa: F401

_register_all()
