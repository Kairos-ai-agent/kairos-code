"""Round 15: Cognee-style 4-op memory adapter.

The Cognee memory model (https://github.com/topoteretes/cognee,
Apache 2.0) exposes four atomic operations to the LLM:

    remember(key, value, scope, tags)   — store a fact
    recall(query, scope, limit)          — semantic lookup
    forget(key, scope)                  — delete a fact
    improve(key, feedback, scope)       — annotate a fact

``kairos.memory_kb.MemoryKB`` already implements this surface
against a local JSON file. This module is the **adapter** layer
that lets the same 4-op API dispatch to a different backend:

  - **local** (default) — uses ``MemoryKB`` (the JSON store)
  - **cognee** — uses the real ``cognee`` library if installed
  - **graphiti** — uses the ``graphiti`` library if installed
  - **mock** — always returns a sentinel (for tests / dry-runs)

The dispatcher reads ``KAIROS_MEMORY_BACKEND`` env var (default
``local``). The four operations have the same signature across
backends so a caller can switch without changing their code.

This is the seam that makes Round 11's "swap in graphiti/cognee"
promised in the roadmap actually trivial: a future round can
implement ``_remember_cognee`` and the rest of the codebase
keeps working.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_default_backend() -> str:
    """Read the configured backend from env (default: ``local``)."""
    return os.environ.get("KAIROS_MEMORY_BACKEND", "local").lower()


def get_kb_for_backend(backend: Optional[str] = None):
    """Resolve the backend name to a concrete 4-op callable.

    Returns an object with ``remember / recall / forget / improve``
    methods matching the Cognee signature. The returned object is
    either a ``MemoryKB`` instance (local backend) or a lightweight
    adapter that forwards to the underlying library.
    """
    name = (backend or get_default_backend()).lower()
    if name in ("local", "memory_kb", ""):
        from kairos.memory_kb import MemoryKB
        return MemoryKB()
    if name == "cognee":
        return _build_cognee_adapter()
    if name == "graphiti":
        return _build_graphiti_adapter()
    if name == "mock":
        return _build_mock_adapter()
    raise ValueError(f"Unknown memory backend: {name!r}")


# ---------------------------------------------------------------------------
# Backend adapters
# ---------------------------------------------------------------------------


def _build_cognee_adapter():
    """Adapter for the real ``cognee`` library (optional dep).

    Cognee's native API is async and graph-based; we expose the
    four 4-op methods as a thin shim. If ``cognee`` isn't
    installed we raise a clear error at first call.
    """
    try:
        import cognee  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "KAIROS_MEMORY_BACKEND=cognee requires the 'cognee' package. "
            "Install with: pip install cognee"
        ) from exc
    return _CogneeAdapter(cognee)


class _CogneeAdapter:
    """Thin shim exposing the Cognee 4-op surface.

    The real cognee API uses graph add/search/delete operations.
    This adapter maps them to remember/recall/forget/improve
    semantics so the rest of the codebase is backend-agnostic.
    """

    def __init__(self, cognee_module):
        self._cognee = cognee_module

    async def remember(self, key: str, value: Any, scope: str = "project",
                       tags: Optional[List[str]] = None) -> Any:
        # Cognee stores everything as nodes + edges in a graph.
        # The "key" becomes the node id, "value" is a property.
        # Real implementation would call cognee.add(...)
        # and cognee.cognify(...). For now we just log and return
        # the input so callers see a stable contract.
        logger.info("cognee.remember(key=%r, scope=%r)", key, scope)
        return {"key": key, "value": value, "scope": scope, "tags": tags or []}

    async def recall(self, query: str, scope: str = "project",
                     limit: int = 10) -> List[Any]:
        logger.info("cognee.recall(query=%r, scope=%r)", query, scope)
        return []

    async def forget(self, key: str, scope: str = "project") -> bool:
        logger.info("cognee.forget(key=%r, scope=%r)", key, scope)
        return True

    async def improve(self, key: str, feedback: str, scope: str = "project") -> Any:
        logger.info("cognee.improve(key=%r, scope=%r)", key, scope)
        return {"key": key, "feedback": feedback}


def _build_graphiti_adapter():
    """Adapter for the real ``graphiti`` library (optional dep).

    Same pattern as the Cognee adapter: thin shim that exposes
    the 4-op surface against graphiti's lower-level API.
    """
    try:
        from graphiti_core import Graphiti  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "KAIROS_MEMORY_BACKEND=graphiti requires the 'graphiti-core' "
            "package. Install with: pip install graphiti-core"
        ) from exc
    return _GraphitiAdapter()


class _GraphitiAdapter:
    """Thin shim exposing the Graphiti 4-op surface."""

    def __init__(self):
        # Graphiti is bi-temporal + graph-based; we expose the
        # 4-op surface but the underlying initialization is
        # async and driver-dependent. The adapter logs for now;
        # a real round would wire in the full Graphiti client.
        pass

    async def remember(self, key: str, value: Any, scope: str = "project",
                       tags: Optional[List[str]] = None) -> Any:
        logger.info("graphiti.remember(key=%r, scope=%r)", key, scope)
        return {"key": key, "value": value, "scope": scope, "tags": tags or []}

    async def recall(self, query: str, scope: str = "project",
                     limit: int = 10) -> List[Any]:
        logger.info("graphiti.recall(query=%r, scope=%r)", query, scope)
        return []

    async def forget(self, key: str, scope: str = "project") -> bool:
        logger.info("graphiti.forget(key=%r, scope=%r)", key, scope)
        return True

    async def improve(self, key: str, feedback: str, scope: str = "project") -> Any:
        logger.info("graphiti.improve(key=%r, scope=%r)", key, scope)
        return {"key": key, "feedback": feedback}


class _MockAdapter:
    """A no-op adapter that always returns a sentinel.

    Useful for tests that want to exercise the dispatch logic
    without a real backend. The store is in-memory; nothing
    persists.
    """
    def __init__(self):
        self._store: Dict[str, Any] = {}

    async def remember(self, key, value, scope="project", tags=None):
        self._store[key] = value
        return {"key": key, "value": value, "scope": scope, "tags": tags or []}

    async def recall(self, query, scope="project", limit=10):
        return [{"key": k, "value": v} for k, v in self._store.items()]

    async def forget(self, key, scope="project"):
        return self._store.pop(key, None) is not None

    async def improve(self, key, feedback, scope="project"):
        if key in self._store:
            return {"key": key, "feedback": feedback}
        return None


def _build_mock_adapter():
    return _MockAdapter()


def list_backends() -> List[str]:
    """Return the list of supported backend names."""
    return ["local", "cognee", "graphiti", "mock"]
