"""LLM provider implementations for Kairos.

Two layers live here:

1. **Standalone helpers** (``OllamaCoder``) — a thin wrapper
   around the Ollama HTTP API used by the benchmark + TUI as a
   drop-in agent. It implements the
   ``.generate(prompt) -> str`` interface that the bench runner
   expects, independent of the long-lived agent runtime.

2. **Integration bridge** (``integration``) — connects the
   short-lived helper layer to the long-lived
   :class:`kairos.llm.model_router.ModelRouter`, so a user can
   click "Use local Ollama" in the Settings drawer and have the
   next project agent actually run on a local LLM.

The existing :class:`kairos.llm.providers.ollama_provider.OllamaProvider`
(registered in the LLM provider registry) is what
:class:`ModelRouter` instantiates when ``role_mappings[coder]``
starts with ``ollama:``. So the chain is::

    Settings drawer
        ↓ set_ollama_provider()
    data/settings.json  ← role_mappings[coder] = "ollama:qwen2.5-coder:7b"
        ↓ ModelRouter.get_provider_for_role("coder")
    OllamaProvider(config)  ← uses the same HTTP API as OllamaCoder
        ↓ agent.complete(messages, ...)
    POST /api/chat on http://127.0.0.1:11434
"""
from .ollama_provider import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OllamaCoder,
    OllamaError,
    is_ollama_running,
    list_models,
    pick_first_coding_model,
)
from .integration import (
    ResolvedProvider,
    clear_ollama_provider,
    current_provider,
    is_ollama_active,
    set_ollama_provider,
)

__all__ = [
    # Standalone benchmark/TUI helper
    "OllamaCoder",
    "OllamaError",
    "is_ollama_running",
    "list_models",
    "pick_first_coding_model",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    # Bridge to ModelRouter
    "ResolvedProvider",
    "set_ollama_provider",
    "clear_ollama_provider",
    "current_provider",
    "is_ollama_active",
]
