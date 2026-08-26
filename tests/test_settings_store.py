"""Tests for the global settings store + API endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.deps as _api_deps
from api.app import app
from api.routes import projects as _projects_routes
from kairos.settings_store import (
    CloudSettings,
    McpSettings,
    MetricsSettings,
    Settings,
    SettingsStore,
    VoiceSettings,
    get_store,
    reset_store,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_store()
    yield
    reset_store()


# ---------------------------------------------------------------------------
# Pure store
# ---------------------------------------------------------------------------


def test_defaults(tmp_path: Path):
    s = SettingsStore(path=tmp_path / "s.json")
    settings = s.get()
    assert settings.voice.ttsProvider == "edge"
    assert settings.voice.ttsVoice == "en-US-AriaNeural"
    assert settings.mcp.enabledServers == ["filesystem"]
    assert settings.cloud.s3Region == "us-east-1"
    assert settings.metrics.showInFooter is True


def test_update_section_merges(tmp_path: Path):
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"voice": {"ttsVoice": "zh-CN-XiaoxiaoNeural", "autoPlay": True}})
    out = s.get()
    # changed fields
    assert out.voice.ttsVoice == "zh-CN-XiaoxiaoNeural"
    assert out.voice.autoPlay is True
    # unchanged fields preserved
    assert out.voice.ttsProvider == "edge"
    assert out.voice.sttLanguage == "en"
    # other sections untouched
    assert out.mcp.permissionPrompt is True


def test_update_top_level_keys(tmp_path: Path):
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"ollama_base_url": "http://my-ollama:11434"})
    assert s.get().ollama_base_url == "http://my-ollama:11434"


def test_persists_to_disk(tmp_path: Path):
    p = tmp_path / "s.json"
    s = SettingsStore(path=p)
    s.update({"voice": {"ttsVoice": "ja-JP-NanamiNeural"}})
    # Reload from disk
    s2 = SettingsStore(path=p)
    assert s2.get().voice.ttsVoice == "ja-JP-NanamiNeural"


def test_reset_restores_defaults(tmp_path: Path):
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"voice": {"ttsVoice": "x"}})
    s.reset()
    assert s.get().voice.ttsVoice == "en-US-AriaNeural"


def test_unknown_section_silently_ignored(tmp_path: Path):
    s = SettingsStore(path=tmp_path / "s.json")
    # No crash on bogus sections
    s.update({"bogus_section": {"k": "v"}})
    # and the file still loads next time
    s2 = SettingsStore(path=tmp_path / "s.json")
    assert s2.get().voice.ttsProvider == "edge"


def test_corrupt_file_recovers_to_defaults(tmp_path: Path):
    p = tmp_path / "s.json"
    p.write_text("{not valid json", encoding="utf-8")
    s = SettingsStore(path=p)
    # Should not raise; just falls back to defaults
    assert s.get().voice.ttsProvider == "edge"


def test_missing_file_uses_defaults(tmp_path: Path):
    s = SettingsStore(path=tmp_path / "nope.json")
    assert s.get().cloud.s3Region == "us-east-1"


def test_provider_env_map_roundtrip(tmp_path: Path):
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"provider_env_map": {"gpt-4o": "OPENAI_API_KEY", "claude-3-5-sonnet": "ANTHROPIC_API_KEY"}})
    s2 = SettingsStore(path=tmp_path / "s.json")
    assert s2.get().provider_env_map == {
        "gpt-4o": "OPENAI_API_KEY", "claude-3-5-sonnet": "ANTHROPIC_API_KEY",
    }


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class FakeProject:
    def __init__(self):
        self.id = "p1"
        self.name = "demo"
        self.metadata: dict = {}
        self.runtime = type("R", (), {"coder_mode": "default"})()


class FakeOrch:
    def __init__(self):
        self.projects = {"p1": FakeProject()}

    def get_project(self, pid):
        return self.projects.get(pid)


@pytest.fixture
def fake_orch():
    real_deps = _api_deps.orchestrator
    real_routes = _projects_routes._orch
    fake = FakeOrch()
    _api_deps.orchestrator = fake
    _projects_routes._orch = lambda: fake
    try:
        yield fake
    finally:
        _api_deps.orchestrator = real_deps
        _projects_routes._orch = real_routes


@pytest.fixture
def client(fake_orch):
    with TestClient(app) as c:
        yield c


def test_api_get_global_settings_default(client):
    r = client.get("/api/projects/settings")
    assert r.status_code == 200
    body = r.json()
    assert "voice" in body
    assert "mcp" in body
    assert "cloud" in body
    assert "metrics" in body


def test_api_post_global_settings_merges(client):
    r = client.post("/api/projects/settings", json={
        "voice": {"ttsVoice": "fr-FR-DeniseNeural"},
        "cloud": {"s3Bucket": "my-bucket"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["voice"]["ttsVoice"] == "fr-FR-DeniseNeural"
    assert body["cloud"]["s3Bucket"] == "my-bucket"
    # unchanged fields preserved
    assert body["voice"]["ttsProvider"] == "edge"


def test_api_get_settings_then_post_roundtrip(client):
    r1 = client.get("/api/projects/settings")
    initial = r1.json()
    r2 = client.post("/api/projects/settings", json={"voice": {"autoPlay": True}})
    after = r2.json()
    assert after["voice"]["autoPlay"] is True
    assert after["voice"]["ttsProvider"] == initial["voice"]["ttsProvider"]


def test_api_get_project_settings(client, fake_orch):
    r = client.get("/api/projects/p1/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == "p1"
    assert body["coder_mode"] == "default"


def test_api_post_project_settings_mode(client, fake_orch):
    r = client.post("/api/projects/p1/settings", json={"coder_mode": "read_only"})
    assert r.status_code == 200
    assert r.json()["coder_mode"] == "read_only"
    # the underlying project was updated
    assert fake_orch.projects["p1"].metadata.get("coder_mode") == "read_only"


def test_api_post_project_settings_unknown_mode_falls_back(client, fake_orch):
    r = client.post("/api/projects/p1/settings", json={"coder_mode": "totally-bogus"})
    assert r.status_code == 200
    # Unknown values fall back to "default"
    assert r.json()["coder_mode"] == "default"


def test_api_project_settings_unknown_project(client):
    r = client.get("/api/projects/nope/settings")
    assert r.status_code == 404
    r = client.post("/api/projects/nope/settings", json={"coder_mode": "sandbox"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Provider panel (round 8)
# ---------------------------------------------------------------------------


def test_provider_nested_round_trip(tmp_path: Path):
    """SettingsDrawer sends a nested ``provider`` object; the store
    must split it into flat fields and emit it nested on read-back."""
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"provider": {
        "active": "ollama",
        "ollamaBaseUrl": "http://gpu-box:11434",
        "ollamaModel": "deepseek-coder-v2:16b",
        "apiKeyEnv": "OPENAI_API_KEY",
    }})
    out = s.get()
    assert out.active_provider == "ollama"
    assert out.provider_ollama_base_url == "http://gpu-box:11434"
    assert out.provider_ollama_model == "deepseek-coder-v2:16b"
    # re-read from disk
    s2 = SettingsStore(path=tmp_path / "s.json")
    assert s2.get().active_provider == "ollama"
    assert s2.get().provider_ollama_model == "deepseek-coder-v2:16b"


def test_provider_to_dict_emits_nested(tmp_path: Path):
    from kairos.settings_store import _to_dict
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"provider": {"active": "anthropic", "apiKeyEnv": "ANTHROPIC_API_KEY"}})
    d = _to_dict(s.get())
    assert d["provider"]["active"] == "anthropic"
    assert d["provider"]["apiKeyEnv"] == "ANTHROPIC_API_KEY"
    assert d["provider"]["ollamaBaseUrl"] == "http://127.0.0.1:11434"  # default


def test_provider_flat_active_provider_still_works(tmp_path: Path):
    """Older clients send ``active_provider`` flat; the store keeps
    accepting it (backwards compat)."""
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"active_provider": "deepseek"})
    assert s.get().active_provider == "deepseek"


def test_provider_partial_patch(tmp_path: Path):
    """Sending only one nested field leaves the others untouched."""
    s = SettingsStore(path=tmp_path / "s.json")
    s.update({"provider": {"active": "ollama", "ollamaModel": "llama3.1:8b"}})
    s.update({"provider": {"ollamaModel": "qwen2.5-coder:7b"}})
    out = s.get()
    # changed
    assert out.provider_ollama_model == "qwen2.5-coder:7b"
    # preserved
    assert out.active_provider == "ollama"


def test_api_post_provider_nested(client):
    """The SettingsDrawer POSTs ``provider: {...}`` and the API
    must accept it and return the nested shape on GET."""
    r = client.post("/api/projects/settings", json={
        "provider": {
            "active": "ollama",
            "ollamaBaseUrl": "http://10.0.0.5:11434",
            "ollamaModel": "qwen2.5-coder:14b",
        },
    })
    assert r.status_code == 200
    body = r.json()
    assert body["provider"]["active"] == "ollama"
    assert body["provider"]["ollamaModel"] == "qwen2.5-coder:14b"
    # GET reads it back
    r2 = client.get("/api/projects/settings")
    assert r2.json()["provider"]["active"] == "ollama"
