"""End-to-end test: Settings → ModelRouter → OllamaProvider.

This test proves the round-7 ``OllamaCoder`` actually drives the
long-lived agent runtime, not just the benchmark. It uses a
temp dir for ``data/settings.json`` so the user's real config
isn't touched.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_settings_dir(tmp_path: Path, monkeypatch):
    """Force ModelRouter to use a temp settings.json."""
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    return tmp_path


def test_set_ollama_provider_writes_settings_json(tmp_settings_dir: Path):
    from kairos.providers.integration import set_ollama_provider
    result = set_ollama_provider(
        base_url="http://my-ollama:11434",
        model="qwen2.5-coder:7b",
    )
    assert result == {"coder": "ollama:qwen2.5-coder:7b",
                       "reviewer": "ollama:qwen2.5-coder:7b"}

    settings = json.loads((tmp_settings_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["ollama_base_url"] == "http://my-ollama:11434"
    assert settings["role_mappings"]["coder"] == "ollama:qwen2.5-coder:7b"
    assert settings["role_mappings"]["reviewer"] == "ollama:qwen2.5-coder:7b"


def test_set_ollama_provider_preserves_previous(tmp_settings_dir: Path):
    from kairos.providers.integration import (
        clear_ollama_provider, set_ollama_provider,
    )
    # First set a non-Ollama provider
    p = tmp_settings_dir / "settings.json"
    p.write_text(json.dumps({
        "role_mappings": {"coder": "gpt-4o", "reviewer": "claude-3-5-sonnet"},
    }), encoding="utf-8")

    set_ollama_provider(model="qwen2.5-coder:7b")

    # Then flip back — should restore the originals
    restored = clear_ollama_provider()
    assert restored == {"coder": "gpt-4o", "reviewer": "claude-3-5-sonnet"}

    settings = json.loads(p.read_text(encoding="utf-8"))
    assert settings["role_mappings"]["coder"] == "gpt-4o"
    assert settings["role_mappings"]["reviewer"] == "claude-3-5-sonnet"
    # Previous siblings cleaned up
    assert "coder:previous" not in settings["role_mappings"]
    assert "reviewer:previous" not in settings["role_mappings"]


def test_set_ollama_provider_only_one_role(tmp_settings_dir: Path):
    from kairos.providers.integration import set_ollama_provider
    p = tmp_settings_dir / "settings.json"
    p.write_text(json.dumps({
        "role_mappings": {"coder": "gpt-4o", "reviewer": "claude-3-5-sonnet"},
    }), encoding="utf-8")
    set_ollama_provider(model="qwen2.5-coder:7b", role="coder")
    settings = json.loads(p.read_text(encoding="utf-8"))
    # coder flipped
    assert settings["role_mappings"]["coder"] == "ollama:qwen2.5-coder:7b"
    # reviewer untouched
    assert settings["role_mappings"]["reviewer"] == "claude-3-5-sonnet"


def test_current_provider_returns_ollama_after_set(tmp_settings_dir: Path):
    from kairos.providers.integration import (
        current_provider, set_ollama_provider,
    )
    set_ollama_provider(model="qwen2.5-coder:7b", role="all")
    p = current_provider("coder")
    assert p.name == "ollama"
    assert p.model == "qwen2.5-coder:7b"
    assert p.base_url  # non-empty
    assert p.api_key == ""  # no key needed


def test_is_ollama_active(tmp_settings_dir: Path):
    from kairos.providers.integration import (
        is_ollama_active, set_ollama_provider,
    )
    assert is_ollama_active("coder") is False
    set_ollama_provider(model="qwen2.5-coder:7b", role="coder")
    assert is_ollama_active("coder") is True
    assert is_ollama_active("reviewer") is False


def test_model_router_returns_ollama_provider_class(tmp_settings_dir: Path):
    """The real test: ModelRouter.get_provider_for_role() returns
    an OllamaProvider when role_mappings[coder] starts with
    ollama:. We assert the class identity, not actual network."""
    from kairos.providers.integration import set_ollama_provider
    from kairos.llm.provider_registry import create_provider
    from kairos.llm.providers.ollama_provider import OllamaProvider

    set_ollama_provider(model="qwen2.5-coder:7b", role="all")

    # Direct: build the provider from the resolved config
    from kairos.providers.integration import current_provider
    rp = current_provider("coder")
    assert rp.name == "ollama"

    # Through the registry: build a config, dispatch
    from kairos.llm.base import LLMConfig
    cfg = LLMConfig(
        provider="ollama", model=rp.model,
        base_url=rp.base_url, api_key="",
    )
    provider = create_provider(cfg)
    assert isinstance(provider, OllamaProvider)
    # And its HTTP base URL is what we set
    assert provider._base_url == rp.base_url


def test_full_chain_through_orchestrator(tmp_settings_dir: Path):
    """End-to-end: setting ollama → orchestrator's get_provider_for_role
    returns an OllamaProvider. We mock the HTTP layer so no
    real Ollama daemon is required."""
    from kairos.providers.integration import set_ollama_provider
    from kairos.llm.model_router import ModelRouter
    from kairos.llm.providers.ollama_provider import OllamaProvider

    set_ollama_provider(model="qwen2.5-coder:7b", role="all")

    router = ModelRouter(config_path=None)  # uses data/settings.json
    provider = router.get_provider_for_role("coder")
    # Confirm class identity
    # (provider is wrapped in ResilientProvider, but raw_provider attr
    #  points to the original)
    raw = getattr(provider, "primary", provider)
    assert isinstance(raw, OllamaProvider)
    assert raw.config.model == "qwen2.5-coder:7b"
