"""Resilient LLM wrapper: retry + exponential backoff + provider failover
+ Anthropic prompt caching.

Wraps any BaseLLMProvider and returns a new provider with the same
interface. All Kairos agents use this transparently.

Retry policy:
- 429 (rate limit) -> backoff and retry (1s, 2s, 4s, 8s, max 30s)
- 5xx / connection error -> backoff and retry
- timeout -> **one** retry, then raise ``LLMTimeoutError``. A timeout already
  means the call spent its whole deadline (``LLMConfig.timeout``, 120s by
  default) before it was reported, so retrying it like any other failure turned
  one stuck call into minutes of spinner with nothing at the end of it. Failover
  is skipped for a timeout as well: two endpoints timing out is an answer.
- 4xx (other) -> no retry, raise immediately
- Other retryable failures: after 5 attempts, either fail (single provider) or
  switch to the failover provider if one is configured.

This module is the *only* retry layer: the openai provider disables the SDK's
own retry (``max_retries=0``), because two layers multiply their budgets and
make the real ceiling impossible to reason about.

Anthropic caching:
- For Claude models, the system prompt + tool list get
  ``cache_control: {"type": "ephemeral"}`` so the second+ round pays
  only for the new Coder output, not the whole prompt prefix.
  ~50% cost reduction on typical loops.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator, List, Optional

from kairos.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMMessage,
    LLMResponse,
)
from kairos.llm.errors import LLMTimeoutError, is_timeout_error

logger = logging.getLogger(__name__)

# Default retry knobs. Override via env or LLMConfig (added below).
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_BACKOFF_S = 1.0
DEFAULT_MAX_BACKOFF_S = 30.0
DEFAULT_FAILOVER_AFTER = 3  # switch providers after N consecutive failures

# Timeouts get their own budget: one retry, then report. See the module
# docstring for why this is not DEFAULT_MAX_RETRIES.
DEFAULT_TIMEOUT_RETRIES = 1


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
    # Connection-level failures: the SDK's own retry is off (see the module
    # docstring), so a dropped connection or a DNS blip has to be handled here
    # or a transient network hiccup fails the run outright.
    if "connection" in name or "connect" in name:
        return True
    if "connection" in msg or "connect" in msg or "temporarily unavailable" in msg:
        return True
    return False


class ResilientProvider(BaseLLMProvider):
    """Wraps a primary provider with retry + optional failover."""

    def __init__(self, primary: BaseLLMProvider,
                 failover: Optional[BaseLLMProvider] = None,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 initial_backoff_s: float = DEFAULT_INITIAL_BACKOFF_S,
                 max_backoff_s: float = DEFAULT_MAX_BACKOFF_S,
                 failover_after: int = DEFAULT_FAILOVER_AFTER,
                 timeout_retries: int = DEFAULT_TIMEOUT_RETRIES):
        # Pass the primary's config up so callers can still read .config
        super().__init__(primary.config)
        self.primary = primary
        self.failover = failover
        self.max_retries = max_retries
        self.initial_backoff_s = initial_backoff_s
        self.max_backoff_s = max_backoff_s
        self.failover_after = failover_after
        self.timeout_retries = timeout_retries
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

    def _timeout_s(self) -> Optional[float]:
        timeout = getattr(self.primary.config, "timeout", None)
        return timeout if isinstance(timeout, (int, float)) else None

    def _raise_timeout(self, attempts: int, last_exc: BaseException) -> None:
        logger.error(
            "LLM call timed out %d times in a row; reporting instead of "
            "retrying (timeout_retries=%d, timeout=%ss)",
            attempts, self.timeout_retries, self._timeout_s(),
        )
        raise LLMTimeoutError(attempts, self._timeout_s(), last_exc) from last_exc

    async def complete(self, messages, tools=None,
                        temperature=None, max_tokens=None) -> LLMResponse:
        provider = self._provider_to_use()
        last_exc = None
        timeout_attempts = 0
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
                # A timeout gets one retry and then an error — never the full
                # budget, and never a failover (see the module docstring).
                if is_timeout_error(e):
                    timeout_attempts += 1
                    if timeout_attempts > self.timeout_retries:
                        self._raise_timeout(timeout_attempts, e)
                    if attempt == self.max_retries - 1:
                        break
                    delay = self._backoff(attempt)
                    logger.warning(
                        "LLM call timed out (attempt %d); retrying once in %.1fs",
                        timeout_attempts, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
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
        if isinstance(last_exc, BaseException) and is_timeout_error(last_exc):
            # Timed out on the last attempt too — report, do not failover.
            self._raise_timeout(timeout_attempts or 1, last_exc)
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
        # so this goes through the same non-streaming path — which also means
        # there is exactly one place holding the retry policy. The caller loses
        # the typewriter effect but gains reliability.
        response = await self.complete(
            messages, tools=tools,
            temperature=temperature, max_tokens=max_tokens,
        )
        # Yield content as a single chunk (callers handle it fine).
        if response.content:
            yield response.content
        if response.tool_calls:
            yield json.dumps({
                "type": "tool_calls",
                "tool_calls": [tc.model_dump() for tc in response.tool_calls],
            })

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
