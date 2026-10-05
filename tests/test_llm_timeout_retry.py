"""A timeout is retried ONCE and then reported — never retried into minutes.

User, verbatim: 「改成超时重试1次，失败报错」.

Before this, a timeout went through the same five-attempt budget as a rate
limit, and the openai SDK added two retries of its own on top of it, so one
stuck call could turn into ~15 requests and ten minutes of spinner before
anything at all was shown to the user.
"""

import asyncio

import pytest

from kairos.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from kairos.llm.errors import LLMTimeoutError, is_timeout_error
from kairos.llm.resilient import ResilientProvider


class _FakeProvider(BaseLLMProvider):
    """Raises the queued exceptions in order, then answers."""

    def __init__(self, errors=(), content="ok"):
        super().__init__(LLMConfig(provider="openai", model="fake",
                                   api_key="test-key", timeout=120))
        self._errors = list(errors)
        self._content = content
        self.calls = 0

    async def complete(self, messages, tools=None, temperature=None,
                       max_tokens=None):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return LLMResponse(content=self._content, model="fake", tool_calls=[])

    async def stream(self, messages, tools=None, temperature=None,
                     max_tokens=None):
        yield self._content

    async def close(self):
        return None


class RateLimitError(Exception):
    """Named like the SDK's, which _is_retryable() matches on."""


class APITimeoutError(Exception):
    """Named like the SDK's, which is_timeout_error() matches on."""


def _wrap(primary, **kw):
    # initial_backoff_s=0 keeps these tests instant.
    kw.setdefault("initial_backoff_s", 0.0)
    return ResilientProvider(primary, **kw)


MSG = [LLMMessage(role="user", content="hi")]


def test_timeout_retries_once_then_reports():
    fake = _FakeProvider([TimeoutError("Request timed out."),
                          TimeoutError("Request timed out.")])
    provider = _wrap(fake)
    with pytest.raises(LLMTimeoutError) as excinfo:
        asyncio.run(provider.complete(MSG))
    # Exactly two attempts: the original plus the one retry.
    assert fake.calls == 2, "a timeout must be attempted twice, no more"
    assert excinfo.value.attempts == 2
    assert "timed out" in str(excinfo.value)
    # The message has to be actionable: how long, and how many times.
    assert "120s" in str(excinfo.value)


def test_timeout_then_success_is_not_an_error():
    """One retry is enough to ride out a transient stall."""
    fake = _FakeProvider([APITimeoutError("timed out")], content="answered")
    provider = _wrap(fake)
    response = asyncio.run(provider.complete(MSG))
    assert response.content == "answered"
    assert fake.calls == 2


def test_timeout_does_not_failover():
    """Two endpoints timing out is an answer, not something to shop around."""
    primary = _FakeProvider([TimeoutError("timed out"), TimeoutError("timed out")])
    failover = _FakeProvider(content="from failover")
    provider = _wrap(primary, failover=failover, failover_after=1)
    with pytest.raises(LLMTimeoutError):
        asyncio.run(provider.complete(MSG))
    assert primary.calls == 2
    assert failover.calls == 0, "a timeout must not trigger provider failover"


def test_other_retryable_errors_keep_the_full_budget():
    """The one-retry rule is about timeouts; a 429 still backs off and retries."""
    fake = _FakeProvider([RateLimitError("429 rate limit")] * 3)
    provider = _wrap(fake, max_retries=3)
    with pytest.raises(RuntimeError):
        asyncio.run(provider.complete(MSG))
    assert fake.calls == 3


def test_non_retryable_still_raises_immediately():
    fake = _FakeProvider([ValueError("bad request")])
    provider = _wrap(fake)
    with pytest.raises(ValueError):
        asyncio.run(provider.complete(MSG))
    assert fake.calls == 1


def test_is_timeout_error_recognises_the_shapes():
    assert is_timeout_error(TimeoutError("Request timed out."))
    assert is_timeout_error(APITimeoutError("APITimeoutError"))
    assert is_timeout_error(RuntimeError("read timed out"))
    assert is_timeout_error(RuntimeError("Deadline exceeded"))
    # …and does not swallow the neighbouring retryable failures.
    assert not is_timeout_error(RateLimitError("429 rate limit"))
    assert not is_timeout_error(ValueError("bad request"))
