"""DeepSeek LLM Provider (uses OpenAI-compatible API)."""

from __future__ import annotations

# DeepSeek uses OpenAI-compatible API, so we just re-register
# the OpenAI provider with DeepSeek defaults.
# The actual registration happens in openai_provider.py
# This file exists for explicit imports and documentation.

from kairos.llm.providers.openai_provider import OpenAIProvider as DeepSeekProvider

__all__ = ["DeepSeekProvider"]
