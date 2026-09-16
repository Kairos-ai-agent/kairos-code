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


def resolve_anthropic_base(cfg: dict[str, Any], *,
                           default: str = "https://api.anthropic.com") -> str:
    """The base ``AnthropicProvider`` expects: it appends ``/v1/messages`` itself.

    The Settings drawer writes the *full* endpoint (``…/v1/messages``), and a
    user may also write just ``…/v1``. Either way the base has to come out as the
    origin that ``/v1/messages`` can be appended to. Stripping only ``/messages``
    leaves ``…/v1`` behind, the provider appends its own path, and the request
    goes to ``/v1/v1/messages`` — a 404 on every call, including the default
    config shipped in the drawer. Hence both suffixes come off here.

    A gateway that serves Anthropic's protocol under a prefix (``https://gw/x``)
    keeps its prefix, so ``https://gw/x/v1/messages`` is what gets called.
    """
    base = resolve_base_url(cfg, default=default, suffix="/v1/messages")
    if base.rstrip("/").endswith("/v1"):
        base = base.rstrip("/")[: -len("/v1")]
    return base.rstrip("/") or default
