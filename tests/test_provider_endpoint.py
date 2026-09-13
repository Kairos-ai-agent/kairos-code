"""The endpoint the UI edits must be the endpoint the client calls.

Reported behaviour: the Settings drawer showed a DeepSeek endpoint, the app kept
calling the provider that had been configured before it, and the user got that
provider's Cloudflare block page. The two fields (`endpointUrl`, which the drawer
edits, and `baseUrl`, which the SDK receives) had drifted apart.
"""
from __future__ import annotations

from kairos.llm.endpoints import resolve_base_url

OPENAI = {"default": "https://api.openai.com/v1", "suffix": "/chat/completions"}
ANTHROPIC = {"default": "https://api.anthropic.com", "suffix": "/messages"}


def test_endpoint_url_wins_over_a_stale_base_url():
    """The exact pair from the report."""
    cfg = {
        "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
        "baseUrl": "https://apihub.agnes-ai.com/v1/chat",
    }
    assert resolve_base_url(cfg, **OPENAI) == "https://api.deepseek.com/v1"


def test_the_sdk_suffix_is_stripped():
    cfg = {"endpointUrl": "https://api.anthropic.com/v1/messages"}
    assert resolve_base_url(cfg, **ANTHROPIC) == "https://api.anthropic.com/v1"


def test_base_url_still_works_when_there_is_no_endpoint_url():
    assert resolve_base_url({"baseUrl": "http://localhost:11434"}, **OPENAI) == "http://localhost:11434"


def test_defaults_when_the_config_is_empty():
    assert resolve_base_url({}, **OPENAI) == "https://api.openai.com/v1"
    assert resolve_base_url({"endpointUrl": "   "}, **ANTHROPIC) == "https://api.anthropic.com"


def test_a_trailing_slash_does_not_produce_a_double_slash():
    cfg = {"endpointUrl": "https://api.deepseek.com/v1/chat/completions/"}
    assert resolve_base_url(cfg, **OPENAI) == "https://api.deepseek.com/v1"


def test_the_settings_model_still_accepts_both_fields():
    """The store's shape is what the resolver reads; keep it honest.

    These are dataclasses, and the docstring on the OpenAI one explains the drift:
    endpointUrl was only ever used by the "test connection" probe while baseUrl
    drove the real chat calls — so editing the endpoint in the drawer changed nothing
    about where the conversation went.
    """
    from dataclasses import asdict, is_dataclass

    from kairos.settings_store import AnthropicProviderConfig, OpenAIProviderConfig

    for model in (OpenAIProviderConfig, AnthropicProviderConfig):
        inst = model()
        data = asdict(inst) if is_dataclass(inst) else vars(inst)
        assert "endpointUrl" in data, "the drawer edits endpointUrl"
        assert "baseUrl" in data, "and the resolver bridges it to the SDK's base url"
        assert resolve_base_url(data, default="https://x.invalid", suffix="/chat/completions")
        assert resolve_base_url(data | {"endpointUrl": ""},
                                default="https://x.invalid", suffix="/chat/completions")
