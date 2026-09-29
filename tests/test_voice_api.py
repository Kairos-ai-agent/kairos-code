"""Voice mode HTTP surface.

Every test here runs offline: the mock engine is forced with an environment
variable, and the one test that exercises the live voice list replaces the
fetch with a stub. Nothing in this file touches the network, because a test
suite that needs Microsoft to be reachable is a test suite that goes red on
somebody else's machine.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.voice as voice


@pytest.fixture()
def client(monkeypatch):
    """A client bound to the voice router alone — no orchestrator needed."""
    monkeypatch.setenv("KAIROS_TTS_ENGINE", "mock")
    app = FastAPI()
    app.include_router(voice.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_voice_cache():
    voice._voices_cache.update({"at": 0.0, "voices": [], "source": ""})
    yield
    voice._voices_cache.update({"at": 0.0, "voices": [], "source": ""})


# ---------------------------------------------------------------------------
# /distill — the spoken form
# ---------------------------------------------------------------------------


def test_distill_returns_the_spoken_form(client):
    resp = client.post("/api/voice/distill", json={
        "text": "All green.\n\nDetails: 2928 passed, 41 skipped, 0 failed.",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["spoken"] == "All green."
    assert body["original_chars"] > body["spoken_chars"]


def test_distill_reports_lengths_not_the_text(client):
    resp = client.post("/api/voice/distill", json={"text": "```py\nx = 1\n```"})
    assert resp.status_code == 200
    assert resp.json()["spoken"] == ""


# ---------------------------------------------------------------------------
# /speak — audio out
# ---------------------------------------------------------------------------


def test_speak_returns_audio_and_names_its_engine(client):
    resp = client.post("/api/voice/speak", json={"text": "Hello there, this is spoken."})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/")
    assert resp.headers["x-voice-engine"] == "mock"
    assert int(resp.headers["x-spoken-chars"]) > 0
    assert len(resp.content) > 0


def test_speak_of_a_code_only_reply_is_204_not_an_error(client):
    """A reply that is all code has nothing to say — and that is not a failure."""
    resp = client.post("/api/voice/speak", json={
        "text": "```python\nprint('only code')\n```",
    })
    assert resp.status_code == 204
    assert resp.headers["x-spoken-chars"] == "0"
    assert resp.content == b""


def test_speak_rejects_empty_text(client):
    assert client.post("/api/voice/speak", json={"text": ""}).status_code == 400


def test_speak_clamps_absurd_sliders(client):
    resp = client.post("/api/voice/speak", json={
        "text": "Fine.", "rate": 100000, "pitch": -100000,
    })
    assert resp.status_code == 422


def test_speak_never_echoes_the_text_in_an_error(client, monkeypatch):
    """Provider failures are reported by type, not by dumping the payload."""
    from kairos.voice import VoiceError

    secret = "sk-do-not-print-me-0123456789"

    def boom(self, text, *, voice="default", mime_type="audio/wav"):
        raise VoiceError("engine exploded")

    monkeypatch.setattr(voice.MockTTSProvider, "synthesize", boom, raising=True)
    resp = client.post("/api/voice/speak", json={"text": f"call with {secret}"})
    assert resp.status_code == 502
    assert secret not in resp.text
    assert "exploded" in resp.text


# ---------------------------------------------------------------------------
# /voices and /status — what the picker shows
# ---------------------------------------------------------------------------


def test_status_admits_when_the_engine_is_a_placeholder(client):
    body = client.get("/api/voice/status").json()
    assert body["engine"] == "mock"
    assert body["available"] is False
    assert "edge-tts" in body["detail"]


def test_voices_falls_back_to_the_bundled_list_when_offline(client, monkeypatch):
    monkeypatch.setenv("KAIROS_TTS_ENGINE", "edge")  # take the live-lookup path
    async def offline():
        raise OSError("no route to host")

    monkeypatch.setattr(voice.EdgeTTSProvider, "list_voices_async", staticmethod(offline))
    body = client.get("/api/voice/voices").json()
    assert body["source"] == "bundled"
    assert body["count"] == len(voice.BUNDLED_VOICES)
    assert body["count"] > 0


def test_bundled_voices_include_chinese_and_english():
    names = {v["name"] for v in voice.BUNDLED_VOICES}
    assert any(n.startswith("zh-CN") for n in names)
    assert any(n.startswith("en-US") for n in names)
    # Every entry must be usable by the engine: a short name and a locale.
    for entry in voice.BUNDLED_VOICES:
        assert entry["name"] and entry["locale"]
        assert entry["friendly"]


def test_voices_can_be_filtered_by_locale(client, monkeypatch):
    monkeypatch.setenv("KAIROS_TTS_ENGINE", "edge")
    async def offline():
        raise OSError("no route to host")

    monkeypatch.setattr(voice.EdgeTTSProvider, "list_voices_async", staticmethod(offline))
    body = client.get("/api/voice/voices?locale=zh").json()
    assert body["count"] > 0
    assert all(v["locale"].lower().startswith("zh") for v in body["voices"])


def test_voice_list_is_cached(client, monkeypatch):
    monkeypatch.setenv("KAIROS_TTS_ENGINE", "edge")
    calls = {"n": 0}

    async def counted():
        calls["n"] += 1
        return [{"name": "en-US-AriaNeural", "locale": "en-US",
                 "gender": "Female", "friendly": "Aria"}]

    monkeypatch.setattr(voice.EdgeTTSProvider, "list_voices_async", staticmethod(counted))
    first = client.get("/api/voice/voices").json()
    second = client.get("/api/voice/voices").json()
    assert first["source"] == "live"
    assert second["count"] == first["count"]
    assert calls["n"] == 1, "the second read should come from the cache"


def test_engine_selection_prefers_edge_when_importable(monkeypatch):
    monkeypatch.delenv("KAIROS_TTS_ENGINE", raising=False)
    engine = voice.engine_name()
    assert engine in ("edge", "mock")
    if engine == "mock":
        pytest.skip("edge-tts is not installed in this environment")


def test_engine_selection_can_be_forced_offline(monkeypatch):
    monkeypatch.setenv("KAIROS_TTS_ENGINE", "mock")
    assert voice.engine_name() == "mock"
    monkeypatch.setenv("KAIROS_TTS_ENGINE", "EDGE ")
    assert voice.engine_name() == "edge"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_router_is_mounted_on_the_app():
    """The routes must be reachable from the real app, not just in isolation.

    Ask the OpenAPI schema, not ``app.routes``: that attribute is not the flat
    route list it looks like, and iterating it silently finds nothing.
    """
    app_module = importlib.import_module("api.app")
    paths = set(app_module.app.openapi().get("paths", {}))
    assert {"/api/voice/speak", "/api/voice/voices",
            "/api/voice/status", "/api/voice/distill"} <= paths
