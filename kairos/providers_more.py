"""More LLM providers (the multi-model CLI-style coverage).

R38.6 §34: presets.ts already lists 8 providers. This module
adds the runtime adapters for the providers that the
existing `kairos/llm/` layer didn't yet wrap:
  - Ollama (local) — fully openAI-compatible at /v1
  - Cohere (via OpenAI-compat proxy) — note: direct ctransformers
  - Groq (fast inference, OpenAI-compatible)
  - Together.ai (open-model API, OpenAI-compatible)
  - Fireworks (open-model API, OpenAI-compatible)
  - Mistral (OpenAI-compatible)
  - xAI / Grok (OpenAI-compatible)
  - Perplexity (OpenAI-compatible with web search)

All use the OpenAI SDK with custom base_url — no need to
install provider-specific SDKs. Just register the preset
endpoint + model and the existing chat client works.

This module is the registry / dispatcher; the actual HTTP
calls go through `kairos.llm.openai_compat.OpenAICompat`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Metadata for one provider in the extended registry."""
    id: str
    label: str
    base_url: str
    default_model: str
    models: List[str] = field(default_factory=list)
    signup_url: str = ""
    docs_url: str = ""
    notes: str = ""


# the multi-model CLI's 75+ provider list, distilled to the most-used
# 8 (besides the ones already in presets.ts).
PROVIDER_REGISTRY: List[ProviderInfo] = [
    ProviderInfo(
        id="ollama-local",
        label="Ollama (local)",
        base_url="http://localhost:11434/v1/chat/completions",
        default_model="qwen2.5-coder:7b",
        models=[
            "qwen2.5-coder:7b", "qwen2.5-coder:32b",
            "llama3.1:8b", "llama3.1:70b", "llama3.2:3b",
            "deepseek-coder-v2:16b", "codestral:22b", "gemma2:27b",
            "mistral:7b", "phi3:14b", "qwen2.5:14b",
        ],
        signup_url="https://ollama.com/",
        docs_url="https://github.com/ollama/ollama",
        notes="Local inference, fully offline, no API key needed",
    ),
    ProviderInfo(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1/chat/completions",
        default_model="llama-3.3-70b-versatile",
        models=[
            "llama-3.3-70b-versatile", "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant", "mixtral-8x7b-32768",
            "gemma2-9b-it", "whisper-large-v3",
        ],
        signup_url="https://console.groq.com/",
        docs_url="https://console.groq.com/docs",
        notes="Fastest inference (LPU), free tier available",
    ),
    ProviderInfo(
        id="together",
        label="Together.ai",
        base_url="https://api.together.xyz/v1/chat/completions",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        models=[
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
            "deepseek-ai/DeepSeek-R1",
            "deepseek-ai/DeepSeek-V3",
        ],
        signup_url="https://api.together.xyz/",
        docs_url="https://docs.together.ai/",
        notes="Open-model API, generous free credits",
    ),
    ProviderInfo(
        id="fireworks",
        label="Fireworks.ai",
        base_url="https://api.fireworks.ai/inference/v1/chat/completions",
        default_model="accounts/fireworks/models/llama-v3p3-70b-instruct",
        models=[
            "accounts/fireworks/models/llama-v3p3-70b-instruct",
            "accounts/fireworks/models/llama-v3p1-405b-instruct",
            "accounts/fireworks/models/deepseek-r1",
            "accounts/fireworks/models/qwen2p5-coder-32b-instruct",
        ],
        signup_url="https://fireworks.ai/",
        docs_url="https://docs.fireworks.ai/",
        notes="Fast open-model inference, $1 free credit",
    ),
    ProviderInfo(
        id="mistral",
        label="Mistral AI",
        base_url="https://api.mistral.ai/v1/chat/completions",
        default_model="mistral-large-latest",
        models=[
            "mistral-large-latest", "mistral-medium-latest",
            "mistral-small-latest", "codestral-latest",
            "pixtral-12b-2409", "ministral-8b-latest",
        ],
        signup_url="https://console.mistral.ai/",
        docs_url="https://docs.mistral.ai/",
        notes="European provider, strong code models (Codestral)",
    ),
    ProviderInfo(
        id="xai",
        label="xAI (Grok)",
        base_url="https://api.x.ai/v1/chat/completions",
        default_model="grok-2-latest",
        models=[
            "grok-2-latest", "grok-2-mini",
            "grok-beta", "grok-vision-beta",
        ],
        signup_url="https://console.x.ai/",
        docs_url="https://docs.x.ai/",
        notes="xAI's Grok models, OpenAI-compatible",
    ),
    ProviderInfo(
        id="perplexity",
        label="Perplexity",
        base_url="https://api.perplexity.ai/v1/chat/completions",
        default_model="llama-3.1-sonar-large-128k-online",
        models=[
            "llama-3.1-sonar-large-128k-online",
            "llama-3.1-sonar-small-128k-online",
            "llama-3.1-sonar-huge-128k-online",
        ],
        signup_url="https://www.perplexity.ai/settings/api",
        docs_url="https://docs.perplexity.ai/",
        notes="Web-search-augmented responses",
    ),
    ProviderInfo(
        id="cohere",
        label="Cohere (compat)",
        base_url="https://api.cohere.ai/compatibility/v1/chat/completions",
        default_model="command-r-plus",
        models=["command-r-plus", "command-r", "command-light"],
        signup_url="https://dashboard.cohere.com/",
        docs_url="https://docs.cohere.com/",
        notes="Cohere via OpenAI-compat endpoint",
    ),
]


def get_provider(provider_id: str) -> Optional[ProviderInfo]:
    for p in PROVIDER_REGISTRY:
        if p.id == provider_id:
            return p
    return None


def list_providers() -> List[ProviderInfo]:
    return list(PROVIDER_REGISTRY)
