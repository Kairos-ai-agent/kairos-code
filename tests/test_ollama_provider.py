"""Tests for the Ollama provider.

These tests use a fake ``urlopen`` so they don't depend on Ollama
actually running. The real ``is_ollama_running`` / ``list_models``
discovery functions are exercised in a smoke test that checks
they don't crash whether Ollama is up or down.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from kairos.providers.ollama_provider import (
    DEFAULT_BASE_URL,
    OllamaCoder,
    OllamaError,
    is_ollama_running,
    list_models,
    pick_first_coding_model,
)


# ---------------------------------------------------------------------------
# HTTP-level helpers
# ---------------------------------------------------------------------------


def test_is_ollama_running_returns_false_on_urlerror():
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        assert is_ollama_running() is False


def test_is_ollama_running_returns_true_on_200():
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    with patch("urllib.request.urlopen", return_value=fake):
        assert is_ollama_running() is True


def test_list_models_parses_response():
    fake = MagicMock()
    fake.__enter__.return_value.read.return_value = json.dumps({
        "models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.1:8b"}]
    }).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=fake):
        out = list_models()
    assert len(out) == 2
    assert out[0]["name"] == "qwen2.5-coder:7b"


def test_list_models_returns_empty_on_error():
    with patch("urllib.request.urlopen", side_effect=OSError()):
        assert list_models() == []


def test_pick_first_coding_model_prefers_qwen():
    fake = MagicMock()
    fake.__enter__.return_value.read.return_value = json.dumps({
        "models": [
            {"name": "llama3.1:8b"},
            {"name": "qwen2.5-coder:7b"},
            {"name": "deepseek-coder:6.7b"},
        ]
    }).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=fake):
        picked = pick_first_coding_model()
    assert picked == "qwen2.5-coder:7b"


def test_pick_first_coding_model_handles_tag_suffix():
    fake = MagicMock()
    fake.__enter__.return_value.read.return_value = json.dumps({
        "models": [{"name": "qwen2.5-coder:latest"}]
    }).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=fake):
        picked = pick_first_coding_model()
    assert picked == "qwen2.5-coder:latest"


def test_pick_first_coding_model_returns_none_when_empty():
    with patch("urllib.request.urlopen", side_effect=OSError()):
        assert pick_first_coding_model() is None


# ---------------------------------------------------------------------------
# OllamaCoder via HTTP fallback
# ---------------------------------------------------------------------------


def test_coder_via_http_sends_chat_request():
    """When the ollama library isn't importable, we use urllib."""
    # Force the "library unavailable" path.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ollama":
            raise ImportError("forced missing for test")
        return real_import(name, *args, **kwargs)

    # Stub the response
    response_body = json.dumps({
        "message": {"role": "assistant", "content": "hi back"},
    }).encode("utf-8")
    fake_response = MagicMock()
    fake_response.__enter__.return_value.read.return_value = response_body

    with patch("builtins.__import__", side_effect=fake_import), \
         patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        coder = OllamaCoder(model="qwen2.5-coder:7b", base_url="http://example:11434")
        out = coder.generate("hello")

    assert out == "hi back"
    # Verify the request URL and body
    call = mock_urlopen.call_args
    req = call[0][0]
    assert req.full_url == "http://example:11434/api/chat"
    body = json.loads(req.data.decode("utf-8"))
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["stream"] is False


def test_coder_raises_ollama_error_on_http_failure():
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ollama":
            raise ImportError("forced missing for test")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import), \
         patch("urllib.request.urlopen", side_effect=OSError("refused")):
        coder = OllamaCoder(model="m", base_url="http://nope:11434")
        with pytest.raises(OllamaError):
            coder.generate("hi")


def test_coder_chat_method_passes_messages():
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ollama":
            raise ImportError
        return real_import(name, *args, **kwargs)

    fake_response = MagicMock()
    fake_response.__enter__.return_value.read.return_value = json.dumps({
        "message": {"content": "ok"}
    }).encode("utf-8")

    with patch("builtins.__import__", side_effect=fake_import), \
         patch("urllib.request.urlopen", return_value=fake_response) as mock:
        coder = OllamaCoder(model="m", base_url="http://x:11434")
        out = coder.chat([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "how are you?"},
        ])
    assert out == "ok"
    body = json.loads(mock.call_args[0][0].data.decode("utf-8"))
    assert len(body["messages"]) == 3


def test_coder_count_tokens_estimates():
    coder = OllamaCoder(model="m", base_url="http://x:11434")
    # char/4 heuristic
    assert coder.count_tokens("") == 1
    assert coder.count_tokens("abcd") == 1
    assert coder.count_tokens("a" * 100) == 25


def test_coder_name_attribute():
    coder = OllamaCoder(model="m", base_url="http://x:11434")
    assert coder.name == "ollama"


# ---------------------------------------------------------------------------
# OllamaCoder via the ollama library
# ---------------------------------------------------------------------------


def test_coder_via_library_calls_client_chat():
    """When the ollama library is importable, we use it."""
    fake_ollama = MagicMock()
    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {"role": "assistant", "content": "lib hi"}
    }
    fake_ollama.Client.return_value = fake_client

    import sys
    saved = sys.modules.get("ollama")
    sys.modules["ollama"] = fake_ollama
    try:
        coder = OllamaCoder(model="lib-model", base_url="http://lib:11434")
        out = coder.generate("ping")
    finally:
        if saved is None:
            sys.modules.pop("ollama", None)
        else:
            sys.modules["ollama"] = saved

    assert out == "lib hi"
    fake_ollama.Client.assert_called_once()
    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs["model"] == "lib-model"
    assert call_kwargs["stream"] is False
    assert call_kwargs["messages"] == [{"role": "user", "content": "ping"}]


def test_coder_via_library_wraps_errors_in_ollama_error():
    fake_ollama = MagicMock()
    fake_client = MagicMock()
    fake_client.chat.side_effect = RuntimeError("connection refused")
    fake_ollama.Client.return_value = fake_client

    import sys
    saved = sys.modules.get("ollama")
    sys.modules["ollama"] = fake_ollama
    try:
        coder = OllamaCoder(model="m", base_url="http://x:11434")
        with pytest.raises(OllamaError):
            coder.generate("hi")
    finally:
        if saved is None:
            sys.modules.pop("ollama", None)
        else:
            sys.modules["ollama"] = saved


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


def test_health_check_succeeds_when_model_present():
    fake = MagicMock()
    fake.__enter__.return_value.read.return_value = json.dumps({
        "models": [{"name": "qwen2.5-coder:7b"}]
    }).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=fake):
        coder = OllamaCoder(model="qwen2.5-coder:7b", base_url="http://x:11434")
        info = coder.health_check()
    assert info["model"] == "qwen2.5-coder:7b"
    assert "qwen2.5-coder:7b" in info["available_models"]


def test_health_check_succeeds_with_tag_suffix_mismatch():
    fake = MagicMock()
    fake.__enter__.return_value.read.return_value = json.dumps({
        "models": [{"name": "qwen2.5-coder:7b"}]
    }).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=fake):
        # request model is "qwen2.5-coder" without tag, available has :7b
        coder = OllamaCoder(model="qwen2.5-coder", base_url="http://x:11434")
        info = coder.health_check()
    assert info["model"] == "qwen2.5-coder"


def test_health_check_raises_when_model_missing():
    fake = MagicMock()
    fake.__enter__.return_value.read.return_value = json.dumps({
        "models": [{"name": "llama3.1:8b"}]
    }).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=fake):
        coder = OllamaCoder(model="gpt-4", base_url="http://x:11434")
        with pytest.raises(OllamaError) as ei:
            coder.health_check()
    assert "not found" in str(ei.value)


def test_health_check_raises_when_ollama_down():
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        coder = OllamaCoder(model="m", base_url="http://nope:11434")
        with pytest.raises(OllamaError):
            coder.health_check()


# ---------------------------------------------------------------------------
# end-to-end: drive the bench with the provider
# ---------------------------------------------------------------------------


def test_bench_runner_against_ollama_coder():
    """Plug OllamaCoder into the bench runner; the HTTP fallback
    returns canned answers and we measure pass@k."""
    from kairos.bench import BenchmarkRunner, humaneval_mini

    # Stub the HTTP layer to return reference solutions for each problem
    response_map = {
        "has_close_elements": "def has_close_elements(numbers, threshold):\n    return False\n",
    }
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ollama":
            raise ImportError
        return real_import(name, *args, **kwargs)

    def fake_urlopen(req, *args, **kwargs):
        body = json.loads(req.data.decode("utf-8"))
        user_msg = body["messages"][-1]["content"]
        # find a problem whose entry_point appears in the prompt
        reply = ""
        for problem in humaneval_mini()[:2]:
            if problem.entry_point in user_msg:
                # Perfect: return reference. For the second problem, return wrong.
                if problem.id == "he-001-has-close-elements":
                    reply = problem.reference_solution
                else:
                    reply = "def _stub(): return None"
                break
        r = MagicMock()
        r.__enter__.return_value.read.return_value = json.dumps({
            "message": {"content": reply}
        }).encode("utf-8")
        return r

    with patch("builtins.__import__", side_effect=fake_import), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        coder = OllamaCoder(model="m", base_url="http://x:11434")
        runner = BenchmarkRunner(agent=coder, label="ollama-test")
        result = runner.run(humaneval_mini()[:2], k=1)
    # First problem passes, second doesn't
    assert result.passed == 1
    assert result.total == 2
