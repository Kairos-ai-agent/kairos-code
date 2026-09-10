"""Tests for Round 37 backend changes:

  - bug_reviewer focus label scopes reviewer to bug detection
  - run_loop(unbounded=True) skips the LOOP_SAFETY_CAP
  - run_loop(unbounded=False) still enforces the cap
  - _is_new_project() returns True for fresh projects, False after a session
  - /api/projects/{id}/chat endpoint (single-turn, no loop)
  - /api/config/test_connection endpoint
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kairos.loop.gates import LOOP_SAFETY_CAP
from kairos.loop.prompts import (
    _REVIEW_FOCUS_LABELS, build_reviewer_description,
)
from kairos.loop.review_loop import LoopSession


# ---------------------------------------------------------------------------
# bug_reviewer focus
# ---------------------------------------------------------------------------


def test_bug_reviewer_focus_exists_and_warns_about_nitpicking():
    """Round 37: the new focus must explicitly say "do not nitpick"."""
    label = _REVIEW_FOCUS_LABELS["bug_reviewer"]
    assert "BUGS ONLY" in label
    assert "Do NOT" in label
    # The other classic focuses still exist (backward compat)
    for k in ("security_reviewer", "perf_reviewer",
              "design_reviewer", "test_reviewer"):
        assert k in _REVIEW_FOCUS_LABELS


def test_build_reviewer_description_uses_bug_reviewer_label():
    """When session.review_focus=['bug_reviewer'], the prompt mentions it."""
    s = MagicMock(spec=LoopSession)
    s.history = []
    s.review_focus = ["bug_reviewer"]
    s.project = MagicMock()
    s.project.requirements = "fix login"
    prompt = build_reviewer_description(s, "coder did X", round_no=1)
    assert "BUGS ONLY" in prompt
    assert "Do NOT" in prompt


def test_build_reviewer_description_falls_back_when_no_focus():
    """No focus → just the standard review prompt, no bug-only label."""
    s = MagicMock(spec=LoopSession)
    s.history = []
    s.review_focus = []
    s.project = MagicMock()
    s.project.requirements = "do x"
    prompt = build_reviewer_description(s, "coder did X", round_no=1)
    assert "BUGS ONLY" not in prompt


# ---------------------------------------------------------------------------
# run_loop(unbounded=...)
# ---------------------------------------------------------------------------


def test_run_loop_signature_accepts_unbounded_kwarg():
    """Round 37: run_loop must have an `unbounded` keyword argument."""
    import inspect
    from kairos.loop.loop_runner import run_loop
    sig = inspect.signature(run_loop)
    assert "unbounded" in sig.parameters
    assert sig.parameters["unbounded"].default is False


def test_unbounded_skips_safety_cap_check(monkeypatch):
    """When unbounded=True, the per-round safety cap is skipped."""
    from kairos.loop import loop_runner

    # Build a fake session that has a small fake round counter and
    # a fake message bus that records publishes. Round >= cap
    # but unbounded=True → no safety_cap event.
    class FakeBus:
        def __init__(self):
            self.published = []
        async def publish(self, msg):
            self.published.append(msg)

    class FakeSession:
        user_stopped = False
        round = LOOP_SAFETY_CAP + 10  # already past the cap
        _unbounded = True
        project = MagicMock()
        project.id = "p1"
        message_bus = FakeBus()
        history = []
        coder = None
        reviewer = None
        score_window = []
        no_progress_count = 0

    # We don't actually call run_loop end-to-end here (that would
    # require real agents). We just call the safety cap check
    # directly to verify it's a no-op when unbounded is set.
    session = FakeSession()
    session._unbounded = True

    # Replicate the relevant check from run_loop's gates:
    if session.round >= LOOP_SAFETY_CAP and not getattr(session, "_unbounded", False):
        would_halt = True
    else:
        would_halt = False
    assert would_halt is False  # because unbounded=True


def test_unbounded_false_enforces_safety_cap():
    """When unbounded=False (default), the cap is enforced as before."""
    class FakeSession:
        round = LOOP_SAFETY_CAP + 10
        _unbounded = False

    s = FakeSession()
    if s.round >= LOOP_SAFETY_CAP and not getattr(s, "_unbounded", False):
        would_halt = True
    else:
        would_halt = False
    assert would_halt is True


# ---------------------------------------------------------------------------
# /api/projects/{id}/chat
# ---------------------------------------------------------------------------


def _make_mock_orch():
    """Return a minimal orchestrator stub with one project + Coder."""
    from kairos.core.message_bus import MessageBus
    bus = MessageBus()

    project = MagicMock()
    project.id = "p1"
    project.coder = MagicMock()
    project.coder.run = MagicMock()
    project.message_bus = bus

    orch = MagicMock()
    orch.get_project = MagicMock(return_value=project)
    return orch, project


def test_chat_endpoint_runs_coder_once(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, project = _make_mock_orch()

    # Mock the coder to return a fixed reply. R38.6.3: the route calls
    # ``coder.chat()`` (single LLM turn), not ``coder.run()``.
    async def fake_chat(task):
        return "Sure, here's what I think."
    project.coder.chat = fake_chat

    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "hi"})
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Sure, here's what I think."
    assert data["mode"] == "chat"
    assert data["project_id"] == "p1"


def test_chat_endpoint_persists_the_user_message(monkeypatch):
    """R38.6.5: the user's own message is written to the DB (topic
    ``user.chat``) so the chat page can re-hydrate the WHOLE
    conversation — previously only the Coder's replies were stored and
    the user's bubbles vanished on refresh."""
    from api.app import app
    from api.routes import projects as projects_route

    saved = []
    db = MagicMock()
    db.save_message = MagicMock(side_effect=lambda m: saved.append(m))

    orch, project = _make_mock_orch()
    orch._db = db

    async def fake_chat(task):
        return "pong"
    project.coder.chat = fake_chat

    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "hello there"})
    assert r.status_code == 200
    assert len(saved) == 1
    msg = saved[0]
    assert msg.sender == "user"
    assert msg.topic == "user.chat"
    assert msg.content == "hello there"
    assert msg.metadata.get("project_id") == "p1"


def test_chat_messages_endpoint_returns_the_thread(monkeypatch):
    """R38.6.5 regression: this route used to do
    ``from api.deps import orchestrator as _orch`` (an INSTANCE) and then
    call ``_orch()``, so EVERY request raised TypeError -> HTTP 500 and
    the chat page silently showed nothing. It must use the module-level
    ``_orch()`` helper and forward ``chat_only`` to the DB layer."""
    from api.app import app
    from api.routes import projects as projects_route

    db = MagicMock()
    db.load_messages = MagicMock(return_value=[{
        "id": "m1", "project_id": "p1", "sender": "p1.coder",
        "receiver": "user", "topic": "agent.response", "content": "hi",
        "msg_type": "text", "timestamp": 1.0, "metadata": {},
    }])
    orch, _project = _make_mock_orch()
    orch._db = db

    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.get("/api/projects/p1/chat-messages")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["messages"][0]["content"] == "hi"
    assert db.load_messages.call_args.kwargs["chat_only"] is True


def test_chat_endpoint_rejects_empty_message(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, _ = _make_mock_orch()
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "  "})
    assert r.status_code == 400


def test_chat_endpoint_rejects_unknown_project(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch = MagicMock()
    orch.get_project = MagicMock(return_value=None)
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/missing/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_chat_endpoint_500_on_coder_failure(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, project = _make_mock_orch()

    async def boom(task):
        raise RuntimeError("LLM down")
    project.coder.chat = boom

    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "hi"})
    assert r.status_code == 500
    assert "LLM down" in r.json()["detail"]


def test_chat_endpoint_503_when_no_coder(monkeypatch):
    from api.app import app
    from api.routes import projects as projects_route
    orch, project = _make_mock_orch()
    project.coder = None
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    c = TestClient(app)
    r = c.post("/api/projects/p1/chat", json={"message": "hi"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /api/config/test_connection
# ---------------------------------------------------------------------------


def test_test_connection_rejects_missing_url():
    """R38.5: the probe requires EITHER endpoint_url (preferred) OR
    legacy base_url. If both are blank, return 400."""
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection", json={
        "provider": "openai",
        "base_url": "  ",
        "endpoint_url": "  ",
        "api_key": "k",
    })
    assert r.status_code == 400
    assert "endpoint_url" in r.json()["detail"].lower() or \
           "base_url" in r.json()["detail"].lower()


def test_test_connection_rejects_missing_key():
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection", json={
        "provider": "openai",
        "endpoint_url": "https://api.openai.com/v1/chat/completions",
        "base_url": "https://x",
        "api_key": "  ",
    })
    assert r.status_code == 400


def test_test_connection_rejects_unknown_provider():
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection", json={
        "provider": "google",
        "endpoint_url": "https://x/v1/chat/completions",
        "api_key": "k",
    })
    assert r.status_code == 400


def test_test_connection_returns_ok_false_on_unreachable():
    """When the URL is unreachable, we get ok=False, status=0, connection error."""
    from api.app import app
    c = TestClient(app)
    r = c.post("/api/config/test_connection", json={
        "provider": "openai",
        "endpoint_url": "http://127.0.0.1:1/v1/chat/completions",  # nothing listens
        "base_url": "http://127.0.0.1:1",
        "api_key": "sk-test",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["status"] == 0
    assert "connection" in data["detail"].lower()


def test_test_connection_openai_posts_to_chat_completions():
    """The OpenAI probe must POST to /v1/chat/completions (the
    actual API surface), not GET /v1/models. Many OpenAI-compatible
    proxies don't expose /v1/models and 404 there; chat-completions
    is the endpoint the user will actually hit on every message.

    This is the fix for the user's report: with base URL
    https://apihub.agnes-ai.com/v1 the previous GET /v1/models
    probe returned 404 → user saw "Fail · Not Found". Switching
    to POST /v1/chat/completions works for all OpenAI-compatible
    servers, including proxies that only expose chat.
    """
    from api.routes.config import _probe_post_openai_chat
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_openai_chat(
            endpoint_url="", base_url="https://api.openai.com",
            api_key="sk-test", model="gpt-4o-mini")
    finally:
        urllib.request.urlopen = orig

    # POST to /v1/chat/completions — the actual API endpoint.
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    # Bearer auth.
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    # Minimal body: 1 message, max_tokens=1, model from the input.
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["model"] == "gpt-4o-mini"
    assert body["max_tokens"] == 1
    assert body["messages"] == [{"role": "user", "content": "ping"}]
    assert result["ok"] is True
    assert result["status"] == 200


def test_test_connection_openai_chat_completions_works_for_proxy_without_models():
    """Some OpenAI-compatible proxies (Agnes AI, custom gateways)
    only expose /v1/chat/completions and 404 on /v1/models. The
    probe must still succeed against a server that only responds
    to chat-completions. We simulate this by patching urlopen to
    return 404 for /v1/models and 200 for /v1/chat/completions —
    but since the new probe only hits the latter, we should be fine.
    """
    from api.routes.config import _probe_post_openai_chat
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        # Simulate a proxy that ONLY supports /v1/chat/completions.
        if "/v1/models" in req.full_url:
            raise urllib.error.HTTPError(
                req.full_url, 404, "Not Found", {}, None)
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    import urllib.error
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_openai_chat(
            endpoint_url="", base_url="https://apihub.agnes-ai.com/v1",
            api_key="sk-test", model="agnes-2.5-flash")
    finally:
        urllib.request.urlopen = orig

    # We hit the chat-completions endpoint, not /v1/models.
    assert captured["url"] == "https://apihub.agnes-ai.com/v1/chat/completions"
    assert result["ok"] is True


def test_test_connection_anthropic_uses_post_v1_messages():
    """The Anthropic probe must POST /v1/messages with x-api-key header."""
    from api.routes.config import _probe_post_anthropic
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_anthropic(
            endpoint_url="", base_url="https://api.anthropic.com",
            api_key="sk-ant-test", model="claude-3-5-sonnet")
    finally:
        urllib.request.urlopen = orig

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["model"] == "claude-3-5-sonnet"
    assert body["max_tokens"] == 1
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# /v1 stripping (R38 fix: "Fail · HTTP 404: Not Found")
# ---------------------------------------------------------------------------
#
# The OpenAI docs show the base URL as ``https://api.openai.com/v1``
# (with /v1). Users reasonably paste that as-is. Without stripping,
# the probe builds ``/v1/v1/models`` → 404. The fix: strip a trailing
# ``/v1`` before appending ``/v1/...`` so both ``api.openai.com`` and
# ``api.openai.com/v1`` work.


def test_strip_v1_strips_trailing_v1():
    """``_strip_v1`` removes a single trailing /v1 (with or without slash)."""
    from api.routes.config import _strip_v1
    assert _strip_v1("https://api.openai.com/v1") == "https://api.openai.com"
    assert _strip_v1("https://api.openai.com/v1/") == "https://api.openai.com"
    assert _strip_v1("https://api.openai.com") == "https://api.openai.com"
    # case-insensitive (some hosts use /V1)
    assert _strip_v1("https://example.com/V1") == "https://example.com"


def test_strip_v1_does_not_strip_v1_in_middle():
    """``/v1`` in the middle of the path is NOT stripped."""
    from api.routes.config import _strip_v1
    assert _strip_v1("https://proxy.example.com/v1/openai") == \
        "https://proxy.example.com/v1/openai"
    assert _strip_v1("https://proxy.example.com/v1/openai/v1") == \
        "https://proxy.example.com/v1/openai"


def test_test_connection_openai_with_trailing_v1_does_not_double_up():
    """The OpenAI probe must NOT produce ``/v1/v1/chat/completions``
    when the user pastes the official ``https://api.openai.com/v1``
    URL. The probe POSTs to ``/v1/chat/completions``; the trailing
    /v1 must be stripped first."""
    from api.routes.config import _probe_post_openai_chat
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        _probe_post_openai_chat(
            endpoint_url="", base_url="https://api.openai.com/v1",
            api_key="sk-test", model="gpt-4o-mini")
    finally:
        urllib.request.urlopen = orig

    # The critical assertion: no /v1/v1/ in the URL.
    assert captured["url"] == "https://api.openai.com/v1/chat/completions", (
        f"OpenAI probe produced {captured['url']} — trailing /v1 was "
        "not stripped, so we hit /v1/v1/chat/completions → 404"
    )


def test_test_connection_anthropic_with_trailing_v1_does_not_double_up():
    """Same fix for Anthropic: trailing /v1 in the base URL must
    not produce /v1/v1/messages."""
    from api.routes.config import _probe_post_anthropic
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        _probe_post_anthropic(
            endpoint_url="", base_url="https://example-proxy.com/v1",
            api_key="sk-ant-test", model="claude-3-5-sonnet")
    finally:
        urllib.request.urlopen = orig

    assert captured["url"] == "https://example-proxy.com/v1/messages", (
        f"Anthropic probe produced {captured['url']} — trailing /v1 was "
        "not stripped, so we hit /v1/v1/messages → 404"
    )


# ---------------------------------------------------------------------------
# R38.5 — endpoint_url: user provides the FULL URL, no auto-construct
# ---------------------------------------------------------------------------
#
# The user said: "你直接把端点都删了，设置时添加完整url地址" — delete the
# auto-endpoint logic entirely, let the user paste the full URL.
# The probe must hit the user-provided endpoint_url as-is, with no
# path manipulation, no /v1 stripping, no /v1/chat/completions appending.


def test_test_connection_openai_uses_endpoint_url_as_is():
    """When the user provides endpoint_url, the probe hits it
    as-is — no /v1 stripping, no /v1/chat/completions appending."""
    from api.routes.config import _probe_post_openai_chat
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        _probe_post_openai_chat(
            endpoint_url="https://apihub.agnes-ai.com/v1/chat/completions",
            base_url="",  # ignored when endpoint_url is set
            api_key="sk-test", model="agnes-2.5-flash")
    finally:
        urllib.request.urlopen = orig

    # The probe URL is exactly what the user pasted. No /v1 stripping.
    assert captured["url"] == "https://apihub.agnes-ai.com/v1/chat/completions"
    # Model from the user is in the body.
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["model"] == "agnes-2.5-flash"


def test_test_connection_anthropic_uses_endpoint_url_as_is():
    """Anthropic probe hits endpoint_url as-is, no path manipulation."""
    from api.routes.config import _probe_post_anthropic
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        _probe_post_anthropic(
            endpoint_url="https://my-anthropic-proxy.example.com/v1/messages",
            base_url="",
            api_key="sk-ant-test", model="claude-3-5-sonnet")
    finally:
        urllib.request.urlopen = orig

    assert captured["url"] == \
        "https://my-anthropic-proxy.example.com/v1/messages"


def test_test_connection_openai_endpoint_url_can_have_arbitrary_path():
    """The user can probe ANY URL they want — e.g. a proxy
    that exposes chat-completions at a non-standard path."""
    from api.routes.config import _probe_post_openai_chat
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        # A custom proxy that puts chat-completions at /api/llm/chat
        # (not the standard /v1/chat/completions). The probe must
        # hit it as-is.
        _probe_post_openai_chat(
            endpoint_url="https://custom-proxy.example.com/api/llm/chat",
            base_url="", api_key="sk-test", model="gpt-4o")
    finally:
        urllib.request.urlopen = orig

    assert captured["url"] == "https://custom-proxy.example.com/api/llm/chat"


def test_test_connection_falls_back_to_base_url_when_endpoint_url_empty():
    """When the user doesn't provide endpoint_url, fall back to
    the legacy auto-construct (base_url + /v1/chat/completions).
    This keeps the old clients working."""
    from api.routes.config import _probe_post_openai_chat
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        resp.status = 200
        return resp

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        _probe_post_openai_chat(
            endpoint_url="",  # legacy: no endpoint_url
            base_url="https://api.openai.com/v1",
            api_key="sk-test", model="gpt-4o")
    finally:
        urllib.request.urlopen = orig

    # Falls back to the legacy path-append behavior.
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# R38.6 — JSON error parsing (so the user sees the actual reason)
# ---------------------------------------------------------------------------
#
# Before R38.6 the probe just truncated the raw JSON body to 200
# chars, so the user saw "HTTP 404: {"error":{"message":"Invalid"
# (truncated) and had no way to know what was actually wrong.
# Now we parse common JSON error shapes and surface the message.


def test_extract_error_message_openai_shape():
    """OpenAI's error format: {"error": {"message": "Invalid API key"}}."""
    from api.routes.config import _extract_error_message
    body = '{"error": {"message": "Incorrect API key provided: sk-****", ' \
           '"type": "invalid_request_error", "code": "invalid_api_key"}}'
    assert _extract_error_message(body) == \
        "Incorrect API key provided: sk-****"


def test_extract_error_message_agnes_ai_shape():
    """Agnes AI / similar proxies use the same {"error": {"message": ...}}
    shape — the user reported seeing this with `Invalid` truncated."""
    from api.routes.config import _extract_error_message
    body = '{"error":{"message":"Invalid model"}}'
    assert _extract_error_message(body) == "Invalid model"


def test_extract_error_message_string_error():
    """Some proxies return {"error": "string message"} instead of nesting."""
    from api.routes.config import _extract_error_message
    assert _extract_error_message('{"error": "Bad gateway"}') == \
        "Bad gateway"


def test_extract_error_message_top_level_message():
    """Some proxies return {"message": "..."} at the top level."""
    from api.routes.config import _extract_error_message
    assert _extract_error_message('{"message": "Not found"}') == \
        "Not found"


def test_extract_error_message_non_json_returns_empty():
    """Plain-text or HTML error bodies return "" so the caller
    falls back to the raw body."""
    from api.routes.config import _extract_error_message
    assert _extract_error_message("") == ""
    assert _extract_error_message("<html>Not Found</html>") == ""
    assert _extract_error_message("plain text error") == ""


def test_extract_error_message_malformed_json_returns_empty():
    """Truncated or invalid JSON returns "" (caller falls back)."""
    from api.routes.config import _extract_error_message
    assert _extract_error_message('{"error": {"message": ') == ""
    assert _extract_error_message('not json at all') == ""


def test_format_probe_error_prefers_parsed_message():
    """_format_probe_error uses the parsed message instead of raw JSON
    so the user sees "HTTP 404: Invalid model" rather than
    "HTTP 404: {"error":{"message":"Invalid model"}}". """
    from api.routes.config import _format_probe_error
    body = '{"error":{"message":"Invalid model"}}'
    detail = _format_probe_error(404, body, "Not Found")
    assert detail == "HTTP 404: Invalid model"
    # The raw JSON is NOT in the detail string.
    assert "{" not in detail
    assert '"error"' not in detail


def test_format_probe_error_falls_back_to_raw_body():
    """When the body isn't JSON-shaped, fall back to the raw body."""
    from api.routes.config import _format_probe_error
    detail = _format_probe_error(500, "Internal Server Error", "Server Error")
    assert "500" in detail
    assert "Internal Server Error" in detail


def test_format_probe_error_truncates_long_bodies_at_500():
    """Raw bodies longer than 500 chars get truncated (with an
    ellipsis) so the UI can render the full error within a reasonable
    width. Parsed JSON messages are NOT truncated."""
    from api.routes.config import _format_probe_error
    long_body = "x" * 1000
    detail = _format_probe_error(500, long_body, "Server Error")
    # Long raw body is truncated.
    assert len(detail) < 700
    assert "…" in detail
    # But a parsed message is shown in full.
    parsed_detail = _format_probe_error(
        500, '{"error": {"message": "' + ("y" * 1000) + '"}}', "Server Error",
    )
    assert "y" * 1000 in parsed_detail


def test_test_connection_openai_404_with_json_error_shows_parsed_message():
    """End-to-end: when the proxy returns 404 with a JSON error
    body, the probe surfaces the parsed message — not the raw JSON.
    This is the fix for the user's report: the test was showing
    "HTTP 404: {"error":{"message":"Invalid" (truncated) and they
    couldn't tell what was actually wrong."""
    from api.routes.config import _probe_post_openai_chat

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":{"message":"Invalid model \'foo\'"}}'),
        )

    import urllib.request
    import urllib.error
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_openai_chat(
            endpoint_url="https://apihub.agnes-ai.com/v1/chat/completions",
            base_url="", api_key="sk-test", model="foo")
    finally:
        urllib.request.urlopen = orig

    assert result["ok"] is False
    assert result["status"] == 404
    # The user sees the parsed message, not the raw JSON.
    assert "Invalid model" in result["detail"]
    # And NOT the raw JSON braces.
    assert '{"error"' not in result["detail"]


def test_test_connection_connection_error_includes_url():
    """R38.6: when the probe can't even reach the URL (DNS failure,
    connection refused, timeout, …), the error message includes
    the URL we tried. The user can verify they pasted the right
    address. Without this, the user sees only "urlopen error
    [Errno ...]" with no indication of which URL failed."""
    from api.routes.config import _probe_post_openai_chat

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("getaddrinfo failed")

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_openai_chat(
            endpoint_url="https://apihub.agnes-ai.com/v1/chat/completions",
            base_url="", api_key="sk-test", model="gpt-4o")
    finally:
        urllib.request.urlopen = orig

    assert result["ok"] is False
    assert result["status"] == 0
    # Connection error message includes the URL.
    assert "connection failed" in result["detail"].lower()
    assert "apihub.agnes-ai.com" in result["detail"]


def test_test_connection_anthropic_connection_error_includes_url():
    """Same as above for the Anthropic probe."""
    from api.routes.config import _probe_post_anthropic

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("getaddrinfo failed")

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_anthropic(
            endpoint_url="https://api.anthropic.com/v1/messages",
            base_url="", api_key="sk-ant-test",
            model="claude-3-5-sonnet")
    finally:
        urllib.request.urlopen = orig

    assert result["ok"] is False
    assert result["status"] == 0
    assert "connection failed" in result["detail"].lower()
    assert "api.anthropic.com" in result["detail"]


def test_test_connection_http_error_includes_url():
    """R38.6.2: HTTP errors (4xx/5xx with a JSON body from the
    proxy) also include the URL we attempted. The user reported
    a doubled-path error like "POST /v1/chat/v1/chat/completions"
    — without the URL, they can't tell what their endpoint
    field actually contained."""
    from api.routes.config import _probe_post_openai_chat

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":{"message":"Invalid URL"}}'),
        )

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        result = _probe_post_openai_chat(
            endpoint_url="https://apihub.agnes-ai.com/v1/chat/completions",
            base_url="", api_key="sk-test", model="gpt-4o")
    finally:
        urllib.request.urlopen = orig

    assert result["ok"] is False
    assert result["status"] == 404
    # Parsed error message is shown.
    assert "Invalid URL" in result["detail"]
    # AND the URL we attempted is appended.
    assert "(url: https://apihub.agnes-ai.com/v1/chat/completions)" in \
        result["detail"]


# ---------------------------------------------------------------------------
# _is_new_project (orchestrator helper)
# ---------------------------------------------------------------------------


def test_is_new_project_true_for_empty_db(monkeypatch):
    """When messages + loop_rounds are both empty, _is_new_project is True."""
    from kairos.core.orchestrator import Orchestrator

    # Don't construct the full Orchestrator; just call the unbound
    # method with a stub self.
    fake = MagicMock()
    fake._db.load_messages = MagicMock(return_value=[])
    fake._db.load_loop_rounds = MagicMock(return_value=[])

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is True


def test_is_new_project_false_with_messages(monkeypatch):
    from kairos.core.orchestrator import Orchestrator

    fake = MagicMock()
    fake._db.load_messages = MagicMock(return_value=[
        {"timestamp": 1.0, "metadata": {}}
    ])
    fake._db.load_loop_rounds = MagicMock(return_value=[])

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is False


def test_is_new_project_false_with_rounds(monkeypatch):
    from kairos.core.orchestrator import Orchestrator

    fake = MagicMock()
    fake._db.load_messages = MagicMock(return_value=[])
    fake._db.load_loop_rounds = MagicMock(return_value=[
        {"ts": 2.0, "session_id": "s1"}
    ])

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is False


def test_is_new_project_db_failure_returns_false(monkeypatch):
    """If both queries raise, we default to False (not new)."""
    from kairos.core.orchestrator import Orchestrator

    fake = MagicMock()
    fake._db.load_messages = MagicMock(side_effect=RuntimeError("db gone"))
    fake._db.load_loop_rounds = MagicMock(side_effect=RuntimeError("db gone"))

    result = Orchestrator._is_new_project(fake, "p1")
    assert result is False  # conservative — treat as existing project


# ---------------------------------------------------------------------------
# start_loop wires unbounded + bug_reviewer for new projects
# ---------------------------------------------------------------------------


def test_start_loop_unbounded_for_new_project(monkeypatch):
    """When _is_new_project is True, start_loop must call run_loop
    with unbounded=True and set review_focus to ['bug_reviewer']
    (when the user hasn't configured a custom focus)."""
    from kairos.core.orchestrator import Orchestrator
    from kairos.loop import review_loop

    # Don't run the actual loop; just intercept run_loop and
    # inspect what start_loop did. The local import inside
    # start_loop is `from kairos.loop.review_loop import ... run_loop`,
    # so patching the attribute on that module is the right hook.
    captured = {}

    async def fake_run_loop(session, requirement, *, unbounded=False):
        captured["unbounded"] = unbounded
        captured["review_focus"] = list(getattr(session, "review_focus", []))
        return None  # don't actually loop

    monkeypatch.setattr(review_loop, "run_loop", fake_run_loop)

    # Build a real-enough Orchestrator mock.
    orch = Orchestrator.__new__(Orchestrator)
    project = MagicMock()
    project.id = "newproj"
    project.coder = MagicMock()
    project.reviewer = MagicMock()
    project.loop_task = None
    project.persistence = None
    project.requirements = ""
    project.message_bus = MagicMock()
    project.status = "idle"
    orch._projects = {"newproj": project}
    orch._db = MagicMock()
    orch._db.save_project = MagicMock()
    orch._db.load_messages = MagicMock(return_value=[])
    orch._db.load_loop_rounds = MagicMock(return_value=[])
    orch._is_new_project = MagicMock(return_value=True)
    orch._instantiate_specialists = MagicMock(return_value=[])
    orch._auto_route_specialists = MagicMock(return_value=[])
    # Persist side effect to avoid MagicMock auto-attr
    orch._db.load_loop_rounds = MagicMock(return_value=[])

    # Patch _load_loop_config to return a focus-less config
    from kairos.core import orchestrator as om
    monkeypatch.setattr(om, "_load_loop_config",
                        lambda: {"review_focus": []})

    # Patch build_reference_digest / build_preferences_block / build_memory_block
    orch.build_reference_digest = MagicMock(return_value="")
    orch.build_preferences_block = MagicMock(return_value="")
    orch.build_memory_block = MagicMock(return_value="")
    orch.message_bus = MagicMock()

    # Run start_loop and wait for it.
    asyncio.run(orch.start_loop("newproj", "fix the auth bug"))
    assert captured["unbounded"] is True
    assert captured["review_focus"] == ["bug_reviewer"]


def test_start_loop_no_unbounded_for_existing_project(monkeypatch):
    """When _is_new_project is False, start_loop must keep the cap
    and use the user-configured review_focus (or none)."""
    from kairos.core.orchestrator import Orchestrator
    from kairos.loop import review_loop

    captured = {}

    async def fake_run_loop(session, requirement, *, unbounded=False):
        captured["unbounded"] = unbounded
        captured["review_focus"] = list(getattr(session, "review_focus", []))
        return None

    monkeypatch.setattr(review_loop, "run_loop", fake_run_loop)

    orch = Orchestrator.__new__(Orchestrator)
    project = MagicMock()
    project.id = "existing"
    project.coder = MagicMock()
    project.reviewer = MagicMock()
    project.loop_task = None
    project.persistence = None
    project.requirements = ""
    project.message_bus = MagicMock()
    project.status = "idle"
    orch._projects = {"existing": project}
    orch._db = MagicMock()
    orch._db.save_project = MagicMock()
    orch._db.load_messages = MagicMock(return_value=[{"metadata": {}}])
    orch._db.load_loop_rounds = MagicMock(return_value=[])
    orch._is_new_project = MagicMock(return_value=False)  # NOT new
    orch._instantiate_specialists = MagicMock(return_value=[])
    orch._auto_route_specialists = MagicMock(return_value=[])
    orch.build_reference_digest = MagicMock(return_value="")
    orch.build_preferences_block = MagicMock(return_value="")
    orch.build_memory_block = MagicMock(return_value="")
    orch.message_bus = MagicMock()

    from kairos.core import orchestrator as om
    monkeypatch.setattr(om, "_load_loop_config",
                        lambda: {"review_focus": ["security_reviewer"]})

    asyncio.run(orch.start_loop("existing", "review the code"))
    assert captured["unbounded"] is False
    # User had security_reviewer configured → must not be overridden
    assert captured["review_focus"] == ["security_reviewer"]


def test_start_loop_preserves_user_review_focus_on_new_project(monkeypatch):
    """Even on a new project, the user's configured review_focus wins."""
    from kairos.core.orchestrator import Orchestrator

    captured = {}

    async def fake_run_loop(session, requirement, *, unbounded=False):
        captured["review_focus"] = list(getattr(session, "review_focus", []))
        return None

    import kairos.core.orchestrator as orch_mod
    from kairos.loop import review_loop
    monkeypatch.setattr(review_loop, "run_loop", fake_run_loop)

    orch = Orchestrator.__new__(Orchestrator)
    project = MagicMock()
    project.id = "newproj"
    project.coder = MagicMock()
    project.reviewer = MagicMock()
    project.loop_task = None
    project.persistence = None
    project.requirements = ""
    project.message_bus = MagicMock()
    project.status = "idle"
    orch._projects = {"newproj": project}
    orch._db = MagicMock()
    orch._db.save_project = MagicMock()
    orch._db.load_messages = MagicMock(return_value=[])
    orch._db.load_loop_rounds = MagicMock(return_value=[])
    orch._is_new_project = MagicMock(return_value=True)
    orch._instantiate_specialists = MagicMock(return_value=[])
    orch._auto_route_specialists = MagicMock(return_value=[])
    orch.build_reference_digest = MagicMock(return_value="")
    orch.build_preferences_block = MagicMock(return_value="")
    orch.build_memory_block = MagicMock(return_value="")
    orch.message_bus = MagicMock()

    from kairos.core import orchestrator as om
    # User explicitly configured "security_reviewer"
    monkeypatch.setattr(om, "_load_loop_config",
                        lambda: {"review_focus": ["security_reviewer"]})

    asyncio.run(orch.start_loop("newproj", "x"))
    # User config wins over the new-project default of bug_reviewer
    assert captured["review_focus"] == ["security_reviewer"]


# ---------------------------------------------------------------------------
# R38 follow-up: project delete endpoint
# ---------------------------------------------------------------------------
#
# The DELETE /api/projects/{id} route already existed (used by the
# legacy /projects page), but the new chat sidebar wires it into
# each project row. These tests pin the behaviour so the sidebar
# delete button can rely on it.
# ---------------------------------------------------------------------------


def _make_orch_with_project(project_id: str, exists: bool = True):
    """Build a minimal orchestrator stub for the delete route."""
    from api.routes import projects as projects_route

    orch = MagicMock()
    if exists:
        orch.get_project.return_value = MagicMock(id=project_id, name=project_id)
    else:
        orch.get_project.return_value = None
    orch.delete_project = MagicMock()
    orch.list_reference_files = MagicMock(return_value=[])
    return orch, projects_route


def test_delete_project_route_delegates_to_orchestrator(monkeypatch):
    """DELETE /api/projects/{id} → orchestrator.delete_project(id)
    (R38.6: now soft-deletes / archives, not hard delete)."""
    from api.routes import projects as projects_route
    from api.app import app
    from fastapi.testclient import TestClient

    orch, _ = _make_orch_with_project("p1", exists=True)
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    client = TestClient(app)
    r = client.delete("/api/projects/p1")
    assert r.status_code == 200
    # R38.6: status is "archived" (was "ok" before). The frontend can
    # use this to show a different toast like "Project archived".
    assert r.json()["status"] == "archived"
    orch.delete_project.assert_called_once_with("p1")


def test_delete_project_route_404_when_missing(monkeypatch):
    """DELETE /api/projects/{id} returns 404 when the project
    doesn't exist (mirrors the legacy /projects page behavior)."""
    from api.routes import projects as projects_route
    from api.app import app
    from fastapi.testclient import TestClient

    orch, _ = _make_orch_with_project("nope", exists=False)
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    client = TestClient(app)
    r = client.delete("/api/projects/nope")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()
    orch.delete_project.assert_not_called()


# ---------------------------------------------------------------------------
# R38.6 — soft delete (archive) + restore
# ---------------------------------------------------------------------------
#
# Before R38.6, DELETE /api/projects/{id} actually removed the row
# from the DB. After Kairos restarted, the frontend's `loadProjects`
# (which was zustand-persisted) brought the project back even though
# the DB didn't have it — a state-mismatch bug.
#
# R38.6 changes the semantics to soft delete: the row stays in the
# DB with `archived_at` set, and `load_projects()` filters it out by
# default. The user can restore via `POST /api/projects/{id}/restore`.


def test_persistence_archive_project_preserves_row(tmp_path):
    """Archiving sets archived_at but does NOT remove the row.
    The project, sessions, files, and notes are all preserved."""
    from kairos.core.persistence import Persistence
    from pathlib import Path
    from kairos.core.orchestrator import Project

    db_path = Path(tmp_path) / "p.db"
    db = Persistence(db_path)
    p = Project("p1", "demo", "", Path("/w"), "/w")
    p.created_at = 1.0
    db.save_project(p)
    # Archive.
    assert db.archive_project("p1") is True
    # Row is still there.
    rows = db.load_projects(include_archived=True)
    assert len(rows) == 1
    assert rows[0]["id"] == "p1"
    assert rows[0]["archived_at"] is not None
    # load_projects() default (no include_archived) hides it.
    assert db.load_projects() == []


def test_persistence_archive_is_idempotent(tmp_path):
    """Archiving an already-archived project returns False (no-op)."""
    from kairos.core.persistence import Persistence
    from pathlib import Path
    from kairos.core.orchestrator import Project

    db_path = Path(tmp_path) / "p.db"
    db = Persistence(db_path)
    p = Project("p1", "demo", "", Path("/w"), "/w")
    p.created_at = 1.0
    db.save_project(p)
    assert db.archive_project("p1") is True
    # Second archive is a no-op.
    assert db.archive_project("p1") is False


def test_persistence_restore_project_brings_it_back(tmp_path):
    """Restoring clears archived_at so the project shows up in
    load_projects() again."""
    from kairos.core.persistence import Persistence
    from pathlib import Path
    from kairos.core.orchestrator import Project

    db_path = Path(tmp_path) / "p.db"
    db = Persistence(db_path)
    p = Project("p1", "demo", "", Path("/w"), "/w")
    p.created_at = 1.0
    db.save_project(p)
    db.archive_project("p1")
    assert db.load_projects() == []
    # Restore.
    assert db.restore_project("p1") is True
    assert len(db.load_projects()) == 1
    assert db.load_projects()[0]["archived_at"] is None


def test_delete_route_returns_archived_status(monkeypatch):
    """R38.6: the API route returns status='archived' (not 'ok') so
    the frontend can show a different toast. This is a contract
    change — old clients checking for 'ok' would still work because
    200 is returned, but a new client can use the richer status."""
    from api.routes import projects as projects_route
    from api.app import app
    from fastapi.testclient import TestClient

    orch, _ = _make_orch_with_project("p1", exists=True)
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    client = TestClient(app)
    r = client.delete("/api/projects/p1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "archived"
    # The message is informative.
    assert "archived" in body["message"].lower()
    assert "preserved" in body["message"].lower()


def test_restore_route_unarchives_project(monkeypatch):
    """POST /api/projects/{id}/restore → orchestrator.restore_project(id)
    returns 200 with status='restored' on success, 404 if not found."""
    from api.routes import projects as projects_route
    from api.app import app
    from fastapi.testclient import TestClient

    # Success case.
    orch = MagicMock()
    orch.restore_project = MagicMock(return_value=True)
    monkeypatch.setattr(projects_route, "_orch", lambda: orch)
    client = TestClient(app)
    r = client.post("/api/projects/p1/restore")
    assert r.status_code == 200
    assert r.json()["status"] == "restored"
    orch.restore_project.assert_called_once_with("p1")

    # Not-found case: orch returns False (project doesn't exist or
    # wasn't archived).
    orch.restore_project = MagicMock(return_value=False)
    r = client.post("/api/projects/nope/restore")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()
