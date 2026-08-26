"""Ollama provider for the benchmark + agent runtimes.

Talks to a local Ollama instance (default ``http://127.0.0.1:11434``)
using the ``ollama`` Python library. Falls back to plain ``urllib``
if the library is missing, so the same code path works on a
barebones Python install.

Two design goals:

  1. **No hard dep on ollama** — if the library isn't installed
     we hit the HTTP API directly. The user can ``pip install
     ollama`` to get nicer streaming + tools, but it isn't
     required for the benchmark to work.

  2. **Match the existing agent interface** — exposes
     ``.generate(prompt: str) -> str`` and optionally
     ``.count_tokens(text: str) -> int``. Anything in Kairos that
     expects these (the bench runner, the reflection module, the
     TUI) can drop an ``OllamaCoder`` in without changes.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5-coder:7b"  # a sensible default for coding


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def is_ollama_running(base_url: str = DEFAULT_BASE_URL, timeout: float = 2.0) -> bool:
    """Return True iff the Ollama server is reachable.

    Pings ``<base_url>/api/tags`` which returns the list of pulled
    models when the daemon is up.
    """
    import urllib.request
    import urllib.error
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def list_models(base_url: str = DEFAULT_BASE_URL, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Return the list of models known to the local Ollama instance.

    Returns an empty list if Ollama isn't reachable.
    """
    import urllib.request
    import urllib.error
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            return list(data.get("models") or [])
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return []


def pick_first_coding_model(base_url: str = DEFAULT_BASE_URL) -> Optional[str]:
    """Return the first model that looks coding-friendly, or None."""
    preferred = [
        "qwen2.5-coder", "qwen-coder", "deepseek-coder", "codellama",
        "starcoder", "codeqwen", "llama3.1", "qwen2.5",
    ]
    models = list_models(base_url)
    names = [m.get("name", "").split(":")[0] for m in models if m.get("name")]
    for p in preferred:
        for n in names:
            if p in n:
                return next(m["name"] for m in models if m["name"].split(":")[0] == n)
    return models[0]["name"] if models else None


# ---------------------------------------------------------------------------
# Token counting (rough — Ollama doesn't expose a tokenizer in the
# public HTTP API). We use a char/4 heuristic.
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OllamaCoder:
    """Minimal Ollama-backed Coder that matches the agent interface.

    Usage::

        coder = OllamaCoder(model="qwen2.5-coder:7b")
        out = coder.generate("def add(a, b): return a + b")
    """

    name = "ollama"

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        # If no model given, try to pick the first coding one.
        if model is None:
            model = pick_first_coding_model(self.base_url) or DEFAULT_MODEL
        self.model = model
        # Cached list of available models (populated lazily)
        self._models_cache: Optional[List[str]] = None

    # -- public --------------------------------------------------------

    def count_tokens(self, text: str) -> int:
        return _estimate_tokens(text)

    def generate(self, prompt: str) -> str:
        """One chat completion, non-streaming. Returns the text content."""
        messages = [{"role": "user", "content": prompt}]
        return self._chat(messages)

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Multi-turn chat completion. ``messages`` is the openai/ollama list."""
        return self._chat(messages)

    def health_check(self) -> Dict[str, Any]:
        """Return Ollama + model status; raises ``OllamaError`` if down."""
        models = list_models(self.base_url, timeout=min(self.timeout_s, 5.0))
        if not models:
            raise OllamaError(
                f"Ollama not reachable at {self.base_url} (no models returned)"
            )
        names = [m.get("name", "") for m in models]
        if self.model not in names and not any(
            n.split(":")[0] == self.model.split(":")[0] for n in names
        ):
            raise OllamaError(
                f"model {self.model!r} not found. Available: {names[:5]}"
            )
        return {
            "base_url": self.base_url,
            "model": self.model,
            "available_models": names,
        }

    # -- internals -----------------------------------------------------

    def _chat(self, messages: List[Dict[str, str]]) -> str:
        try:
            return self._chat_via_library(messages)
        except ImportError:
            return self._chat_via_http(messages)

    def _chat_via_library(self, messages: List[Dict[str, str]]) -> str:
        import ollama  # type: ignore
        client = ollama.Client(host=self.base_url, timeout=self.timeout_s)
        try:
            resp = client.chat(
                model=self.model,
                messages=messages,
                stream=False,
                options={
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                },
            )
        except Exception as exc:
            raise OllamaError(f"ollama chat failed: {exc}") from exc
        msg = (resp or {}).get("message") or {}
        return str(msg.get("content", "")).strip()

    def _chat_via_http(self, messages: List[Dict[str, str]]) -> str:
        """Plain HTTP fallback when the ollama library isn't installed."""
        import urllib.request
        import urllib.error

        url = self.base_url + "/api/chat"
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise OllamaError(f"ollama HTTP request failed: {exc}") from exc
        msg = (data or {}).get("message") or {}
        return str(msg.get("content", "")).strip()


class OllamaError(RuntimeError):
    """Raised when the Ollama provider can't reach the server / model."""


# ---------------------------------------------------------------------------
# CLI helper: pick a working model and print its name
# ---------------------------------------------------------------------------


def main() -> int:  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(description="Ollama provider health check")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    if not is_ollama_running(args.base_url):
        print(f"Ollama NOT running at {args.base_url}")
        return 1
    models = list_models(args.base_url)
    print(f"Ollama OK at {args.base_url}, {len(models)} model(s):")
    for m in models[:20]:
        print(f"  - {m.get('name')}  (size={m.get('size', '?')})")
    picked = pick_first_coding_model(args.base_url)
    if picked:
        print(f"\nRecommended coding model: {picked}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
