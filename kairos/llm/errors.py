"""Turn a provider failure into something a human can act on.

A provider that answers with an HTML page — a Cloudflare block, a captive portal, a
`base_url` that points at the site's homepage instead of its API — makes the HTTP
client raise an exception whose message *is* that page. It used to be passed through
untouched, so the chat showed several kilobytes of markup (one real report was
Cloudflare's "Sorry, you have been blocked" page, Ray ID and all) with the one useful
fact — an HTML page came back, not JSON — buried inside it.
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_BLOCKED_HOST = re.compile(
    r"unable to access\s*(?:<[^>]+>\s*)*([a-z0-9.-]+\.[a-z]{2,})", re.I
)
_RAY_ID = re.compile(r"Ray ID:?\s*(?:<[^>]+>\s*)*([0-9a-f]{8,})", re.I)

_BLOCK_MARKERS = (
    "sorry, you have been blocked",
    "attention required",
    "cloudflare",
    "checking your browser",
)

_AUTH_MARKERS = (
    "authentication fails",
    "authentication failed",
    "invalid api key",
    "incorrect api key",
    "no api key",
    "unauthorized",
    "invalid_api_key",
)

_AUTH_HINT = (
    "the API key is missing or was rejected — set it in Settings "
    "(provider.apiKey) or provide the environment variable named by "
    "provider.apiKeyEnv"
)


def _body_of(exc: BaseException) -> str:
    """The response body if the exception carries one, else the exception text."""
    response = getattr(exc, "response", None)
    for source in (response, exc):
        for attr in ("text", "content", "body"):
            blob = getattr(source, attr, None)
            if isinstance(blob, bytes):
                blob = blob.decode("utf-8", errors="replace")
            if isinstance(blob, str) and blob.strip():
                return blob
    return str(exc)


def _status_of(exc: BaseException) -> str:
    for source in (exc, getattr(exc, "response", None)):
        code = getattr(source, "status_code", None)
        if isinstance(code, int):
            return f"HTTP {code} "
    return ""


def describe_provider_error(exc: BaseException, *, limit: int = 280) -> str:
    """A short, actionable description of a provider failure — never a wall of HTML.

    HTML is recognised and summarised rather than truncated, because truncating a
    block page yields markup soup; the host and the Ray ID are the parts worth
    keeping, and they are what tells the user where to look.
    """
    body = _body_of(exc)
    status = _status_of(exc)
    head = body.lstrip()[:400].lower()
    looks_html = body.lstrip()[:1] == "<" or "<html" in head or "<!doctype" in head

    if looks_html:
        lower = body.lower()
        if any(marker in lower for marker in _BLOCK_MARKERS):
            host_match = _BLOCKED_HOST.search(body)
            ray_match = _RAY_ID.search(body)
            host = host_match.group(1) if host_match else "the endpoint"
            bits = [f"{status}the provider blocked this client (Cloudflare)" if status
                    else "the provider blocked this client (Cloudflare)"]
            bits.append(f"it returned an HTML block page for {host}"
                        + (f", Ray ID {ray_match.group(1)}" if ray_match else ""))
            bits.append("the request never reached the API — check base_url, the API key, "
                        "and the network this machine uses (a VPN or proxy IP can be "
                        "blocked even when the site opens in a browser)")
            return " — ".join(bits)

        title_match = _TITLE.search(body)
        title = _WHITESPACE.sub(" ", title_match.group(1)).strip() if title_match else ""
        return (f"{status}the provider returned an HTML page instead of JSON"
                + (f" ({title})" if title else "")
                + " — base_url should point at the API, not at the site's homepage")

    collapsed = _WHITESPACE.sub(" ", body).strip()
    if len(collapsed) > limit:
        collapsed = collapsed[:limit].rstrip() + " …"

    # A bare 401 is the most common first-run failure — a fresh install has no key —
    # and "Authentication Fails (governor)" tells the user nothing about what to do.
    lower = collapsed.lower()
    code = status.strip()
    if code in ("HTTP 401", "HTTP 403") or any(m in lower for m in _AUTH_MARKERS):
        return f"{status}{collapsed} — {_AUTH_HINT}" if collapsed else f"{status}{_AUTH_HINT}"

    return f"{status}{collapsed}" if status else collapsed
