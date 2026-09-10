"""R38.7 — a routed provider must always be usable (credentials included).

`loop_config.difficulty_routing: true` sends a *trivial* requirement down the
`fast` tier via ``get_provider_for_task_strict("fast")``, which deliberately
bypasses the user's active provider. The built-in tiers in
``kairos/config/models_config.yaml`` carry a model name but no api_key /
base_url (they were written for env-var keys), so the Coder came up with an
empty key and every loop ended on turn 1 with:

    No API key for deepseek-chat. Configure in Settings.

The router now fills the gap from the active provider: a tier that has no
credentials at all falls back to the active provider wholesale, and a tier
that has its own endpoint but no key just borrows the key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kairos.llm.base import LLMConfig  # noqa: E402
from kairos.llm.model_router import ModelRouter  # noqa: E402

MODELS_YAML = ROOT / "kairos" / "config" / "models_config.yaml"
ACTIVE_KEY = "sk-active-provider-key"


@pytest.fixture
def router(tmp_path, monkeypatch):
    """A router reading a temp settings.json with a configured provider."""
    (tmp_path / "settings.json").write_text(json.dumps({
        "active_provider": "openai",
        "provider": {
            "active": "openai",
            "openai": {
                "apiKey": ACTIVE_KEY,
                "model": "deepseek-chat",
                "baseUrl": "https://api.deepseek.com/v1",
                "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
            },
        },
    }), encoding="utf-8")
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    return ModelRouter(config_path=MODELS_YAML)


def _usable(cfg: LLMConfig) -> bool:
    return bool(cfg.api_key) and bool(cfg.base_url) and cfg.api_key != "«redacted:sk-…»"


@pytest.mark.parametrize("tier", ["fast", "default", "strong"])
def test_every_tier_resolves_to_a_usable_provider(router, tier):
    cfg = router.get_provider_for_task_strict(tier).config
    assert _usable(cfg), f"{tier} tier is not callable: {cfg}"


def test_fast_tier_keeps_its_own_model(router):
    """`fast` exists to be cheap — it must still be deepseek-chat, not the
    active model pulled in wholesale."""
    cfg = router.get_provider_for_task_strict("fast").config
    assert cfg.model == "deepseek-chat"
    assert cfg.base_url and "deepseek" in cfg.base_url


def test_credential_less_tier_falls_back_to_the_active_provider(router):
    """`default` (gpt-4o, no key, no endpoint) would otherwise send gpt-4o to
    whatever endpoint the borrowed key belongs to."""
    cfg = router.get_provider_for_task_strict("default").config
    assert cfg.model == "deepseek-chat"
    assert cfg.api_key == ACTIVE_KEY


def test_tier_with_own_endpoint_only_borrows_the_key(router):
    router._model_configs["probe-tier"] = LLMConfig(
        provider="openai", model="probe-model", base_url="https://my-proxy.test/v1")
    cfg = router.get_provider_for_task_strict("probe-tier").config
    assert cfg.api_key == ACTIVE_KEY            # borrowed
    assert cfg.model == "probe-model"           # tier's own choices kept
    assert cfg.base_url == "https://my-proxy.test/v1"


def test_role_providers_are_usable_too(router):
    for role in ("coder", "reviewer", "default"):
        cfg = router.get_provider_for_role(role).config
        assert _usable(cfg), f"{role} provider is not callable: {cfg}"


def test_no_active_provider_does_not_crash(router):
    """Nothing to borrow → leave the config alone (previous behaviour)."""
    router._model_configs.pop("__active__", None)
    cfg = router.get_provider_for_task_strict("fast").config
    assert cfg.model == "deepseek-chat"      # untouched, just unauthenticated
