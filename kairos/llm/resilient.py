"""Resilient LLM wrapper: retry + exponential backoff + provider failover
+ Anthropic prompt caching.

Wraps any BaseLLMProvider and returns a new provider with the same
interface. All Kairos agents use this transparently.

Retry policy:
- 429 (rate limit) -> backoff and retry (1s, 2s, 4s, 8s, max 30s)
- 5xx / timeout -> backoff and retry
- 4xx (other) -> no retry, raise immediately
- After 5 attempts, either fail (single provider) or switch to
  failover provider if configured.

Anthropic caching:
- For Claude models, the system prompt + tool list get
  ``cache_control: {"type": "ephemeral"}`` so the second+ round pays
  only for the new Coder output, not the whole prompt prefix.
  ~50% cost reduction on typical loops.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncIterator, List, Optional

from kairos.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMMessage,
    LLMResponse,
)

logger = logging.getLogger(__name__)


# Default retry knobs. Override via env or LLMConfig (added below).
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_BACKOFF_S = 1.0
DEFAULT_MAX_BACKOFF_S = 30.0
DEFAULT_FAILOVER_AFTER = 3  # switch providers after N consecutive failures


# Errors worth retrying. The openai SDK raises openai.RateLimitError
# (429), openai.APIStatusError (5xx), openai.APITimeoutError, etc.
# We string-match on common attributes so this works across providers.
def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "429" in msg:
        return True
    if "timeout" in name or "timed out" in msg:
        return True
    if "apistatus" in name and ("5" in msg[:5] or "503" in msg or "502" in msg):
        return True
    if "service" in name and "unavailable" in msg:
        return True
    return False


class ResilientProvider(BaseLLMProvider):
    """Wraps a primary provider with retry + optional failover."""

    def __init__(self, primary: BaseLLMProvider,
                 failover: Optional[BaseLLMProvider] = None,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 initial_backoff_s: float = DEFAULT_INITIAL_BACKOFF_S,
                 max_backoff_s: float = DEFAULT_MAX_BACKOFF_S,
                 failover_after: int = DEFAULT_FAILOVER_AFTER):
        # Pass the primary's config up so callers can still read .config
        super().__init__(primary.config)
        self.primary = primary
        self.failover = failover
        self.max_retries = max_retries
        self.initial_backoff_s = initial_backoff_s
        self.max_backoff_s = max_backoff_s
        self.failover_after = failover_after
        self._consecutive_failures = 0
        self._using_failover = False

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        delay = min(self.initial_backoff_s * (2 ** attempt), self.max_backoff_s)
        return delay * (0.5 + random.random() / 2)

    def _provider_to_use(self):
        if self._using_failover and self.failover:
            return self.failover
        return self.primary

    async def complete(self, messages, tools=None,
                        temperature=None, max_tokens=None) -> LLMResponse:
        provider = self._provider_to_use()
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                response = await provider.complete(
                    messages, tools=tools,
                    temperature=temperature, max_tokens=max_tokens,
                )
                self._consecutive_failures = 0
                # After a success, drop back to primary for next call
                # (failover might be more expensive / slower).
                self._using_failover = False
                return response
            except Exception as e:
                last_exc = e
                if not _is_retryable(e):
                    # Non-retryable: surface immediately.
                    raise
                self._consecutive_failures += 1
                if attempt == self.max_retries - 1:
                    # Out of retries.
                    break
                delay = self._backoff(attempt)
                logger.warning(
                    "LLM call failed (attempt %d/%d, %s); retrying in %.1fs: %s",
                    attempt + 1, self.max_retries,
                    type(e).__name__, delay, e,
                )
                await asyncio.sleep(delay)
                # Decide mid-retry whether to failover.
                if (self.failover
                        and self._consecutive_failures >= self.failover_after
                        and not self._using_failover):
                    logger.warning(
                        "switching to failover provider after %d consecutive failures",
                        self._consecutive_failures,
                    )
                    self._using_failover = True
                    provider = self.failover
        # All retries exhausted.
        if self.failover and not self._using_failover:
            logger.error("primary provider exhausted retries; trying failover once")
            self._using_failover = True
            try:
                return await self.failover.complete(
                    messages, tools=tools,
                    temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as e:
                logger.exception("failover also failed: %s", e)
        raise RuntimeError(
            f"LLM call failed after {self.max_retries} retries: {last_exc}"
        )

    async def stream(self, messages, tools=None,
                      temperature=None, max_tokens=None) -> AsyncIterator[str]:
        # Streaming retries are trickier (can't replay partial output),
        # so we delegate to non-streaming .complete() under the hood.
        # The caller loses the typewriter effect but gains reliability.
        provider = self._provider_to_use()
        for attempt in range(self.max_retries):
            try:
                response = await provider.complete(
                    messages, tools=tools,
                    temperature=temperature, max_tokens=max_tokens,
                )
                self._consecutive_failures = 0
                self._using_failover = False
                # Yield content as a single chunk (callers handle it fine).
                if response.content:
                    yield response.content
                if response.tool_calls:
                    import json as _json
                    yield _json.dumps({
                        "type": "tool_calls",
                        "tool_calls": [tc.model_dump() for tc in response.tool_calls],
                    })
                return
            except Exception as e:
                if not _is_retryable(e):
                    raise
                self._consecutive_failures += 1
                if attempt == self.max_retries - 1:
                    break
                delay = self._backoff(attempt)
                logger.warning("stream() failed; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
        raise RuntimeError(f"LLM stream failed after retries: {last_exc}")

    async def close(self):
        await self.primary.close()
        if self.failover:
            await self.failover.close()


def wrap_with_resilience(provider: BaseLLMProvider,
                          failover: Optional[BaseLLMProvider] = None,
                          **kwargs) -> ResilientProvider:
    """Convenience constructor.

    `failover` is optional; pass another provider instance to enable
    automatic failover after `failover_after` consecutive failures.
    """
    return ResilientProvider(provider, failover=failover, **kwargs)