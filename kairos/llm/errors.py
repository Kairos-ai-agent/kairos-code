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

# --- Context-window exhaustion ------------------------------------------------
#
# Every provider reports "your request is bigger than my window" its own way,
# and the wording changes with the model. Kairos needs to recognise it because
# the correct response is NOT to fail: it is to compact and try again (see
# kairos.context_governor). A missed match means a dead session, so the marker
# list errs wide — a false positive only costs one wasted compaction attempt.
_CONTEXT_LENGTH_MARKERS = (
    "context_length_exceeded",
    "context length exceeded",
    "maximum context length",
    "max context length",
    "exceeds the maximum",
    "prompt is too long",
    "prompt too long",
    "input is too long",
    "too many tokens",
    "too many total tokens",
    "reduce the length of the messages",
    "reduce your prompt",
    "request entity too large",
    "string too long",
    "context window",
)


def is_context_length_error(exc: BaseException) -> bool:
    """True when *exc* means "this request did not fit in the context window".

    Works on the raw provider exception and on ``ResilientProvider``'s
    ``RuntimeError("LLM call failed after N retries: <original>")`` wrapper,
    because the wrapper keeps the original text in its message.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    if code == 413:
        return True

    body = _body_of(exc).lower()
    if not any(marker in body for marker in _CONTEXT_LENGTH_MARKERS):
        return False
    # A 400 that names the context window is the canonical shape. Any other
    # status (or none, e.g. the retry wrapper) still counts when the text is
    # this specific — the cost of being wrong is one compaction round-trip.
    return True


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


# --- Timeouts -----------------------------------------------------------------
#
# A timeout is not like other retryable failures: the call already spent its
# whole deadline (LLMConfig.timeout, 120s by default) before it was reported.
# Retrying it five times, with the openai SDK's own retry on top, turned one
# stuck call into ~15 requests and ten minutes of spinner with no answer at the
# end of it. So a timeout gets exactly one retry, and then it is reported.

_TIMEOUT_MARKERS = (
    "timeout",
    "timed out",
    "deadline exceeded",
    "read timed out",
)


def is_timeout_error(exc: BaseException) -> bool:
    """True when *exc* means "the provider did not answer in time".

    Matched on the class name as well as the text: the openai SDK raises
    ``APITimeoutError``, httpx raises ``ReadTimeout``/``ConnectTimeout``, and
    the retry wrapper keeps only the original message.
    """
    name = type(exc).__name__.lower()
    if "timeout" in name or "timedout" in name:
        return True
    return any(marker in _body_of(exc).lower() for marker in _TIMEOUT_MARKERS)


class LLMTimeoutError(RuntimeError):
    """The provider missed its deadline twice in a row, so we stopped.

    Subclasses ``RuntimeError`` so callers already catching the retry
    wrapper's error keep working; ``str()`` is what the user ends up reading,
    so it says what happened, how many times, and how to get more patience.
    """

    def __init__(self, attempts: int, timeout_s: float | None,
                 last: BaseException):
        self.attempts = attempts
        self.timeout_s = timeout_s
        self.last = last
        within = f"{timeout_s:g}s" if timeout_s else "its deadline"
        super().__init__(
            f"LLM call timed out {attempts} times in a row: the provider did "
            f"not answer within {within}. A timeout is retried once and then "
            f"reported instead of retried until the request is minutes old. "
            f"Last error: {last}"
        )
