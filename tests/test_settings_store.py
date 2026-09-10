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


# ---------------------------------------------------------------------------
# R38.6 — openai / anthropic per-provider config round-trip
# ---------------------------------------------------------------------------
#
# The frontend's R37+ redesign stores LLM settings as
# {provider: {active, openai: {endpointUrl, baseUrl, apiKey, model},
#             anthropic: {...}}}. Before R38.6 the backend's
# ``update()`` only handled the legacy R8 shape
# (provider.ollamaBaseUrl, etc.) and silently DROPPED the new
# keys — so the user's settings were lost on backend restart.
# These tests pin the new behavior: write the new shape, restart
# the store, the new keys are still there.


def test_settings_store_persists_openai_anthropic_configs(tmp_path: Path):
    """R38.6: the per-provider openai / anthropic configs
    (endpointUrl, baseUrl, apiKey, model) must round-trip through
    SettingsStore so the user's LLM settings survive a backend
    restart. Before this fix, the backend's update() only handled
    the legacy R8 shape (provider.ollamaBaseUrl, etc.) and silently
    dropped the new keys."""
    from kairos import settings_store as _ss
    import json

    path = tmp_path / "settings.json"

    # Round 1: write the new shape, read it back.
    _ss._store = _ss.SettingsStore(path)
    patch = {
        "provider": {
            "active": "openai",
            "openai": {
                "endpointUrl": "https://api.example.com/v1/chat/completions",
                "baseUrl": "https://api.example.com/v1",
                "apiKey": "sk-test-openai-1234",
                "model": "example-model",
            },
            "anthropic": {
                "endpointUrl": "https://api.anthropic.com/v1/messages",
                "baseUrl": "https://api.anthropic.com",
                "apiKey": "sk-ant-test-5678",
                "model": "claude-3-5-sonnet-latest",
            },
        },
    }
    _ss._store.update(patch)

    # Verify the file on disk has the new keys (in both the
    # nested ``provider.openai`` and the top-level
    # ``provider_openai`` positions — the migration code reads
    # both shapes).
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["provider"]["openai"]["model"] == "example-model"
    assert on_disk["provider"]["openai"]["apiKey"] == "sk-test-openai-1234"
    assert on_disk["provider"]["openai"]["endpointUrl"] == \
        "https://api.example.com/v1/chat/completions"
    assert on_disk["provider"]["anthropic"]["model"] == \
        "claude-3-5-sonnet-latest"
    # Top-level mirror for legacy readers (e.g. the model router).
    assert on_disk["provider_openai"]["model"] == "example-model"
    assert on_disk["provider_anthropic"]["model"] == \
        "claude-3-5-sonnet-latest"

    # Round 2: simulate a backend restart — read the file back
    # through a fresh SettingsStore instance.
    _ss._store = _ss.SettingsStore(path)
    s = _ss._store.get()
    assert s.provider_openai.apiKey == "sk-test-openai-1234"
    assert s.provider_openai.model == "example-model"
    assert s.provider_openai.endpointUrl == \
        "https://api.example.com/v1/chat/completions"
    assert s.provider_anthropic.apiKey == "sk-ant-test-5678"
    assert s.provider_anthropic.model == "claude-3-5-sonnet-latest"
    assert s.active_provider == "openai"


def test_settings_store_legacy_provider_shape_migrates_to_nested(tmp_path: Path):
    """A settings.json from the R8 era (flat active_provider /
    provider_ollama_base_url / etc.) should be readable as the
    new R37+ nested shape — backwards-compat."""
    from kairos import settings_store as _ss
    import json

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "active_provider": "anthropic",
        "provider_ollama_base_url": "http://localhost:11434",
        "provider_ollama_model": "qwen2.5",
        "provider_api_key_env": "ANTHROPIC_API_KEY",
    }), encoding="utf-8")
    _ss._store = _ss.SettingsStore(path)
    s = _ss._store.get()
    # Legacy fields round-trip.
    assert s.active_provider == "anthropic"
    assert s.provider_ollama_base_url == "http://localhost:11434"
    # New nested fields are still defaulted.
    assert s.provider_openai.model == "gpt-4o"
    assert s.provider_anthropic.model == "claude-3-5-sonnet-latest"


def test_api_post_provider_nested_openai_anthropic(client):
    """R38.6: POST /api/projects/settings with the new R37+ nested
    shape (provider.openai.* and provider.anthropic.*) must round-
    trip through the API and persist. Before the fix, these keys
    were silently dropped — the user thought their settings were
    saved but the backend was throwing them away."""
    r = client.post("/api/projects/settings", json={
        "provider": {
            "active": "openai",
            "openai": {
                "endpointUrl": "https://api.example.com/v1/chat/completions",
                "baseUrl": "https://api.example.com/v1",
                "apiKey": "sk-test-1234",
                "model": "example-model",
            },
            "anthropic": {
                "endpointUrl": "https://api.anthropic.com/v1/messages",
                "baseUrl": "https://api.anthropic.com",
                "apiKey": "sk-ant-5678",
                "model": "claude-3-5-sonnet-latest",
            },
        },
    })
    assert r.status_code == 200
    # GET reads it back — the user's settings survived the
    # round-trip.
    r2 = client.get("/api/projects/settings")
    body = r2.json()
    assert body["provider"]["openai"]["apiKey"] == "sk-test-1234"
    assert body["provider"]["openai"]["model"] == "example-model"
    assert body["provider"]["anthropic"]["apiKey"] == "sk-ant-5678"
    assert body["provider"]["anthropic"]["model"] == "claude-3-5-sonnet-latest"


def test_model_router_loads_r37_openai_config_from_settings(tmp_path: Path, monkeypatch):
    """R38.6: the ModelRouter should pick up the openai / anthropic
    configs from settings.json (set by the Settings UI) and register
    them as usable LLMConfigs. Without this, the LLM call used
    whatever was in custom_models / api_keys (legacy fields) and
    ignored the user's UI-configured provider."""
    from kairos.llm import model_router as _mr
    from kairos.llm.model_router import ModelRouter
    import json

    # The model_router reads from kairos.llm.model_router.settings_path()
    # which honors KAIROS_DATA_DIR at call time (not import time).
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "provider": {
            "active": "openai",
            "openai": {
                "apiKey": "sk-test-1234",
                "baseUrl": "https://api.example.com/v1",
                "model": "example-model",
            },
            "anthropic": {
                "apiKey": "sk-ant-5678",
                "baseUrl": "https://api.anthropic.com",
                "model": "claude-3-5-sonnet-latest",
            },
        },
    }), encoding="utf-8")
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    router = ModelRouter(config_path=None)
    active = router._model_configs.get("__active__")
    assert active is not None, (
        "ModelRouter did not register the R37+ openai config "
        "from settings.json. The user's LLM settings will be "
        "ignored by the actual LLM call."
    )
    assert active.model == "example-model"
    assert active.api_key == "sk-test-1234"
    assert active.base_url == "https://api.example.com/v1"
    assert active.provider == "openai"


def test_model_router_picks_active_provider_from_settings(tmp_path: Path, monkeypatch):
    """When active=anthropic, the __active__ config should be the
    anthropic one, not the openai one."""
    from kairos.llm.model_router import ModelRouter
    import json

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "provider": {
            "active": "anthropic",
            "openai": {"apiKey": "sk-o", "baseUrl": "https://x", "model": "gpt"},
            "anthropic": {"apiKey": "sk-a", "baseUrl": "https://y", "model": "claude"},
        },
    }), encoding="utf-8")
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    router = ModelRouter(config_path=None)
    active = router._model_configs.get("__active__")
    assert active is not None
    assert active.provider == "anthropic"
    assert active.model == "claude"
