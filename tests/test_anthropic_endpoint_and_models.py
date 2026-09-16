"""The Anthropic endpoint: one /v1, and a model list that is really fetched.

Two defects lived here and neither was visible from the settings screen:

1. ``resolve_base_url(anthropic_cfg, suffix="/messages")`` left ``…/v1`` on the
   base, and ``AnthropicProvider`` appends ``/v1/messages`` itself — so every
   call went to ``/v1/v1/messages``. The drawer's own default
   (``https://api.anthropic.com/v1/messages``) produced it, which means a fresh
   Anthropic setup never worked.

2. The "fetch model list" button hardcoded ``protocol: "openai"``, so the
   backend's Anthropic branch was unreachable, and that branch answered with a
   hardcoded list of MiniMax models for *any* Anthropic endpoint.

These tests pin the arithmetic, the wiring through ModelRouter, and the fetch
against a real local HTTP server.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kairos.llm.endpoints import resolve_anthropic_base, resolve_base_url

SEEN: list[tuple[str, str, dict]] = []
LOCK = threading.Lock()


# --------------------------------------------------------------------------
# 1. The arithmetic
# --------------------------------------------------------------------------


@pytest.mark.parametrize("written", [
    "https://api.anthropic.com/v1/messages",   # the drawer's default
    "https://api.anthropic.com/v1",            # bare /v1
    "https://api.anthropic.com",               # the origin
    "https://api.anthropic.com/",              # trailing slash
])
def test_the_drawer_value_resolves_to_the_origin(written: str) -> None:
    """All four shapes a user can end up with must give the same base."""
    assert resolve_anthropic_base({"endpointUrl": written}) == "https://api.anthropic.com"


def test_a_gateway_prefix_survives() -> None:
    """A proxy that serves Anthropic's protocol under a path keeps that path."""
    assert resolve_anthropic_base({"endpointUrl": "https://gw.corp/anthropic/v1/messages"}) == \
        "https://gw.corp/anthropic"
    assert resolve_anthropic_base({"endpointUrl": "https://gw.corp/anthropic/v1"}) == \
        "https://gw.corp/anthropic"


def test_baseurl_governs_when_endpointurl_is_empty() -> None:
    assert resolve_anthropic_base({"baseUrl": "https://proxy.example/anthropic"}) == \
        "https://proxy.example/anthropic"


def test_the_provider_builds_v1_exactly_once() -> None:
    """The whole point: base + "/v1/messages" must not double the prefix.

    AnthropicProvider does ``f"{base}/v1/messages"``. Asserting on that
    concatenation is what makes this a regression test rather than a tautology —
    it is the string that goes on the wire.
    """
    for written in ("https://api.anthropic.com/v1/messages", "https://api.anthropic.com/v1"):
        base = resolve_anthropic_base({"endpointUrl": written})
        url = f"{base}/v1/messages"
        assert url == "https://api.anthropic.com/v1/messages", url
        assert url.count("/v1/") == 1, url


def test_the_openai_side_is_untouched() -> None:
    """endpointUrl still wins over a stale baseUrl, and the suffix still comes off."""
    cfg = {"endpointUrl": "https://api.deepseek.com/v1/chat/completions",
           "baseUrl": "https://api.openai.com/v1"}
    assert resolve_base_url(cfg, default="https://api.openai.com/v1",
                            suffix="/chat/completions") == "https://api.deepseek.com/v1"


# --------------------------------------------------------------------------
# 2. The wiring: what ModelRouter hands the provider
# --------------------------------------------------------------------------

MODELS_YAML = Path(__file__).resolve().parent.parent / "kairos" / "yamls" / "models.yaml"


def _router_with(settings: dict, tmp_path: Path):
    from kairos.llm.model_router import ModelRouter

    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return ModelRouter(config_path=MODELS_YAML)


def test_the_router_hands_the_provider_an_origin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    router = _router_with({"provider": {
        "active": "anthropic",
        "anthropic": {
            "endpointUrl": "https://api.anthropic.com/v1/messages",
            "apiKey": "sk-ant-not-real", "model": "claude-sonnet-4-5-20250929",
        },
    }}, tmp_path)
    cfg = router._model_configs.get("__r37_anthropic__")
    assert cfg is not None, "the R37 anthropic config was not registered"
    assert cfg.base_url == "https://api.anthropic.com"
    assert f"{cfg.base_url}/v1/messages" == "https://api.anthropic.com/v1/messages"


def test_a_custom_anthropic_gateway_is_honoured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    router = _router_with({"provider": {
        "active": "anthropic",
        "anthropic": {
            "endpointUrl": "https://my-gw.example/proxy/v1/messages",
            "apiKey": "k", "model": "claude-x",
        },
    }}, tmp_path)
    cfg = router._model_configs.get("__r37_anthropic__")
    assert cfg.base_url == "https://my-gw.example/proxy"


# --------------------------------------------------------------------------
# 3. The fetch, against a real server
# --------------------------------------------------------------------------


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        with LOCK:
            SEEN.append((self.command, self.path, dict(self.headers)))
        if self.path.rstrip("/") == "/v1/models":
            if self.headers.get("x-api-key"):
                payload = {"data": [
                    {"id": "claude-opus-4-1-20250805", "display_name": "Claude Opus 4.1"},
                    {"id": "claude-sonnet-4-5-20250929", "display_name": "Claude Sonnet 4.5"},
                ]}
            else:
                payload = {"object": "list", "data": [{"id": "b-model"}, {"id": "a-model"}]}
            body = json.dumps(payload).encode()
            self.send_response(200)
        else:
            body = b'{"error":"not found"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def stub():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SEEN.clear()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _fetch(base: str, protocol: str, key: str = "stub-key") -> dict:
    from api.routes.config import FetchModelsRequest, fetch_custom_models

    return asyncio.run(fetch_custom_models(FetchModelsRequest(
        base_url=base, api_key=key, protocol=protocol)))


def test_anthropic_fetch_asks_the_origin_for_v1_models(stub) -> None:
    """The drawer sends …/v1 (it strips /messages); the route must ask /v1/models."""
    result = _fetch(f"{stub}/v1", "anthropic")
    asked = [p for _m, p, _h in SEEN]
    assert asked == ["/v1/models"], asked
    assert result["count"] == 2
    # Anthropic's field is display_name; the old code read `name` and showed blanks.
    assert result["models"][0] == {"id": "claude-opus-4-1-20250805",
                                   "name": "Claude Opus 4.1"}
    with LOCK:
        hdrs = {k.lower(): v for k, v in SEEN[0][2].items()}
    assert hdrs.get("x-api-key") == "stub-key"
    assert hdrs.get("anthropic-version")


def test_anthropic_fetch_accepts_the_full_endpoint_too(stub) -> None:
    result = _fetch(f"{stub}/v1/messages", "anthropic")
    assert result["count"] == 2
    assert [p for _m, p, _h in SEEN] == ["/v1/models"]


def test_anthropic_fetch_reports_a_missing_endpoint_instead_of_inventing_models(stub) -> None:
    """A gateway without /v1/models gets an honest answer, not a stale list.

    The old branch returned five hardcoded MiniMax models for any Anthropic
    endpoint, which is worse than nothing: the user picks one and the call fails
    with a model-not-found instead of telling them the list is unavailable.
    """
    result = _fetch(f"{stub}/nope/v1/messages", "anthropic")
    assert result["models"] == []
    assert result["count"] == 0
    assert "error" in result and "404" in result["error"]
    assert "model id" in result["note"]


def test_openai_fetch_still_works(stub) -> None:
    result = _fetch(f"{stub}/v1", "openai")
    assert [p for _m, p, _h in SEEN] == ["/v1/models"]
    assert [m["id"] for m in result["models"]] == ["a-model", "b-model"]
    with LOCK:
        hdrs = {k.lower(): v for k, v in SEEN[0][2].items()}
    assert hdrs.get("authorization") == "Bearer stub-key"
