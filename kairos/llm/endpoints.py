"""One endpoint, stored twice — stop the two fields from disagreeing.

Provider configs carry both ``endpointUrl`` (the full URL the Settings drawer edits)
and ``baseUrl`` (what gets handed to the SDK). They describe the same thing, and when
a settings file has them out of step the client silently calls the *other* host. A
real report: the endpoint had been switched to DeepSeek in the UI, every request kept
going to the previous provider, and that provider answered with a Cloudflare block
page — so the error the user saw had nothing to do with what they had configured.

The UI edits ``endpointUrl``, so that wins; ``baseUrl`` remains the fallback for
files and profiles written before it existed.
"""
from __future__ import annotations

from typing import Any


def resolve_base_url(cfg: dict[str, Any], *, default: str, suffix: str) -> str:
    """Base URL for the SDK, derived from whichever endpoint field is present.

    ``suffix`` is the path the full endpoint carries and the SDK's base must not:
    ``/chat/completions`` for OpenAI-compatible providers, ``/messages`` for Anthropic.
    """
    endpoint = str(cfg.get("endpointUrl") or "").strip()
    if endpoint:
        # Normalise first: "…/chat/completions/" would otherwise not match the
        # suffix and the full path would be handed to the SDK as a base URL.
        endpoint = endpoint.rstrip("/")
        if suffix and endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
        return endpoint.rstrip("/") or default

    base = str(cfg.get("baseUrl") or "").strip()
    return base.rstrip("/") or default
