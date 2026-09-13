"""Provider errors must arrive readable, not as a wall of HTML.

A real report: the chat showed `[500] Coder chat failed: <!DOCTYPE html> … Sorry, you
have been blocked … Cloudflare Ray ID: …` — several kilobytes of markup, because the
HTTP client puts the response body in the exception and the route interpolated it
verbatim. The one useful fact (an HTML page came back, not JSON) was buried in it.
"""
from __future__ import annotations

from kairos.llm.errors import describe_provider_error


class _FakeResponse:
    def __init__(self, text: str, status_code: int | None = None):
        self.text = text
        if status_code is not None:
            self.status_code = status_code


class _FakeError(Exception):
    def __init__(self, message: str, response: _FakeResponse | None = None,
                 status_code: int | None = None):
        super().__init__(message)
        if response is not None:
            self.response = response
        if status_code is not None:
            self.status_code = status_code


CF_BLOCK = """<!DOCTYPE html><html class="no-js" lang="en-US"><head>
<title>Attention Required! | Cloudflare</title></head><body>
<h1 data-translate="block_headline">Sorry, you have been blocked</h1>
<h2 class="cf-subheadline"><span data-translate="unable_to_access">You are unable to access</span> agnes-ai.com</h2>
<p>Cloudflare Ray ID: <strong class="font-semibold">a3a4a47f0e0bdbcf</strong></p>
</body></html>"""


def test_cloudflare_block_page_becomes_a_sentence():
    exc = _FakeError("Client error '403 Forbidden'", _FakeResponse(CF_BLOCK, 403))
    out = describe_provider_error(exc)

    assert "Cloudflare" in out
    assert "agnes-ai.com" in out, "the blocked host is the useful part"
    assert "a3a4a47f0e0bdbcf" in out, "the Ray ID is what the site owner needs"
    assert "HTTP 403" in out
    assert "<" not in out, "no markup may survive"
    assert len(out) < 500, f"still too long to read in a chat bubble: {len(out)}"


def test_generic_html_is_identified_with_its_title():
    exc = _FakeError("boom", _FakeResponse("<html><head><title>Welcome</title></head></html>"))
    out = describe_provider_error(exc)
    assert "HTML page instead of JSON" in out
    assert "Welcome" in out
    assert "base_url" in out, "it should say what to check"
    assert "<" not in out


def test_a_long_plain_error_is_truncated():
    exc = _FakeError("x" * 5000)
    out = describe_provider_error(exc)
    assert len(out) <= 320
    assert out.endswith("…")


def test_a_short_plain_error_is_left_alone():
    assert describe_provider_error(_FakeError("connection reset by peer")) == "connection reset by peer"


def test_status_code_is_prefixed_when_available():
    out = describe_provider_error(_FakeError("nope", status_code=429))
    assert out.startswith("HTTP 429 ")


def test_auth_failure_says_what_to_do():
    """The exact first-run failure: DeepSeek's 401 body, verbatim."""
    exc = _FakeError("Authentication Fails (governor)", status_code=401)
    out = describe_provider_error(exc)
    assert "API key is missing or was rejected" in out, out
    assert "Settings" in out and "apiKeyEnv" in out, "it should say where to set it"
    assert len(out) < 400


def test_auth_failure_is_recognised_without_a_status_code():
    """Some providers only say it in the body."""
    out = describe_provider_error(_FakeError("Invalid API key provided"))
    assert "API key is missing or was rejected" in out
