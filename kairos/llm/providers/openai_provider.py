"""OpenAI LLM Provider (also compatible with OpenRouter, DeepSeek, etc.)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, AsyncIterator, List, Optional

from kairos.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.provider_registry import ProviderRegistry
from kairos.llm.providers.base import format_messages_for_openai

logger = logging.getLogger(__name__)

#: How much of the hidden-reasoning channel a UI hint carries. The full channel
#: can run to tens of thousands of characters on one turn and only its tail is
#: ever displayed, so shipping more would burn WebSocket bandwidth for nothing.
REASONING_TAIL_CHARS = 400


def _reasoning_tail(text: str, n: int = REASONING_TAIL_CHARS) -> str:
    """The last ``n`` characters of ``text`` (what a rolling line shows)."""
    if not text:
        return ""
    return text if len(text) <= n else text[-n:]


def _reasoning_field(message: Any) -> str:
    """The hidden-reasoning text on a message, under either field name.

    OpenAI-compatible servers disagree: DeepSeek and LM Studio use
    ``reasoning_content``; some (vLLM's ``--reasoning-parser``, a few Ollama
    shims) use ``reasoning``. Reading only one of them silently dropped the
    fallback answer on the other. Returns "" when neither is present.
    """
    for attr in ("reasoning_content", "reasoning"):
        value = getattr(message, attr, None)
        if value:
            return value if isinstance(value, str) else str(value)
    return ""


def _normalize_openai_base_url(url: str) -> str:
    """Trim a base_url so the OpenAI SDK's own "/chat/completions" append
    never doubles the path.

    The SDK builds ``<base_url>/chat/completions``. If a base_url already
    ends with a chat path (e.g. ``.../v1/chat`` from a frontend that
    stripped only the last segment of ``.../v1/chat/completions``), the
    request becomes ``.../v1/chat/chat/completions`` (404). Normalize the
    likely variants down to the API root.
    """
    if not url:
        return url
    base = url.rstrip("/")
    base = re.sub(r"/v1/chat/completions$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"/chat/completions$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"/v1/chat$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"/chat$", "", base, flags=re.IGNORECASE)
    return base or url.rstrip("/")


class OpenAIProvider(BaseLLMProvider):
    """OpenAI-compatible provider using the openai Python SDK."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        from openai import AsyncOpenAI

        # The SDK's own retry is off on purpose: ResilientProvider owns the
        # retry policy (including the one-retry-then-report rule for timeouts),
        # and two layers multiply their budgets — the SDK's 2 retries on top of
        # 5 attempts is up to 15 requests for one stuck call, which is how a
        # single timeout became ten minutes of spinner.
        kwargs = {"api_key": config.api_key or "sk-placeholder", "max_retries": 0}
        if config.base_url:
            kwargs["base_url"] = _normalize_openai_base_url(config.base_url)
        # Some OpenAI-compatible proxies (e.g. Cloudflare-fronted ones)
        # reject the SDK's default "OpenAI/Python" user-agent. Allow an
        # override via KAIROS_OPENAI_USER_AGENT so the user can present a
        # different client identity without a rebuild.
        ua = os.environ.get("KAIROS_OPENAI_USER_AGENT", "").strip()
        if ua:
            kwargs["default_headers"] = {"User-Agent": ua}
        self._client = AsyncOpenAI(**kwargs)
        # ``stream_options.include_usage`` is how OpenAI-compatible endpoints
        # report token counts for streamed calls — and streaming is the path
        # the agent loop actually uses, so without it every streamed call was
        # accounted as 0 tokens in the cost ledger. Endpoints that don't know
        # the field reject the whole request rather than ignoring it, so the
        # capability is negotiated once per provider instance in ``stream``.
        self._stream_usage_supported = True

    @staticmethod
    def _rejects_stream_options(exc: Exception) -> bool:
        """True when ``exc`` looks like "I don't know stream_options".

        Only decides whether to retry the same request without the field. A
        false positive costs one extra request before the real error surfaces,
        so the check is deliberately generous; a false negative would only
        mean losing usage reporting again, never a wrong result.
        """
        text = str(exc).lower()
        if "stream_options" in text:
            return True
        return any(word in text for word in (
            "unknown", "unrecognized", "unexpected", "extra fields",
            "invalid request", "unsupported",
        ))

    async def complete(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        kwargs = {
            "model": self.config.model,
            "messages": format_messages_for_openai(messages),
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
            kwargs["tool_choice"] = "auto"

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            # Enhanced error handling for better diagnostics. Summarise the provider's
            # reply instead of interpolating it: when the endpoint answers with an HTML
            # block page, ``{e}`` *is* that page, and it used to end up whole in the chat.
            if "text/plain" in str(e).lower() or "not json" in str(e).lower():
                from kairos.llm.errors import describe_provider_error

                raise type(e)(
                    "API returned text/plain instead of JSON. Check base_url and model "
                    f"availability: {describe_provider_error(e)}"
                ) from e
            raise
        
        choice = response.choices[0]
        # Hidden reasoning on the non-streaming path arrives whole on the same
        # message. Count it (an empty reply that spent its budget thinking must
        # be explainable) AND keep the tail so the caller can show a thinking
        # line. It never touches ``content`` below -- unless ``content`` came
        # back empty AND there is reasoning to fall back on (see below).
        reasoning_text = _reasoning_field(choice.message)

        # Small local models (LM Studio's gemma-4-e4b-…, Ollama and friends)
        # routinely write their WHOLE answer into the hidden-reasoning channel
        # and leave ``content`` as "". Kairos reads only ``content``, so the
        # user saw "模型没有返回任何内容" while the model had, in fact, answered.
        # The fallback is deliberately narrow: it fires only when ``content``
        # is empty/whitespace AND reasoning is non-empty, and it never merges
        # the two when content exists (the invariant the module has always
        # guarded). ``reply_from_reasoning`` records which channel answered.
        content_text = choice.message.content or ""
        reply_from_reasoning = False
        if not content_text.strip() and reasoning_text.strip():
            content_text = reasoning_text
            reply_from_reasoning = True
            logger.warning(
                "provider returned empty content with %d chars of reasoning; "
                "using the reasoning channel as the reply (model=%s)",
                len(reasoning_text), self.config.model,
            )

        # Parse tool calls
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = []
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = tc.function.arguments
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        return LLMResponse(
            content=content_text,
            model=response.model,
            usage=response.usage.model_dump() if response.usage else {},
            finish_reason=choice.finish_reason or "",
            tool_calls=tool_calls,
            # Thinking models put their hidden reasoning on a separate field.
            # Counting it is what lets an empty answer be explained instead of
            # looking like a silent failure; the tail rides along so a
            # non-streaming caller can show a live thinking line.
            reasoning_chars=len(reasoning_text),
            reasoning_tail=_reasoning_tail(reasoning_text),
            reply_from_reasoning=reply_from_reasoning,
        )

    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        kwargs = {
            "model": self.config.model,
            "messages": format_messages_for_openai(messages),
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
            kwargs["tool_choice"] = "auto"
        if self._stream_usage_supported:
            kwargs["stream_options"] = {"include_usage": True}
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not (self._stream_usage_supported
                    and self._rejects_stream_options(exc)):
                raise
            # The endpoint doesn't know the field. Drop it for good and send
            # the same request again: this costs one extra round trip on the
            # first call of a provider that lacks support, and nothing after.
            logger.info(
                "endpoint rejects stream_options; token usage will not be "
                "reported for streamed calls (model=%s): %s",
                self.config.model, exc,
            )
            self._stream_usage_supported = False
            kwargs.pop("stream_options", None)
            response = await self._client.chat.completions.create(**kwargs)

        # Accumulate tool_call deltas
        tool_calls_by_index: dict = {}
        finish_reason = ""
        reasoning_chars = 0
        reasoning_accum = ""
        content_chars = 0
        usage: dict = {}
        async for chunk in response:
            # Some endpoints attach usage to the last content chunk; others
            # send a trailing chunk with no choices at all.
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = (chunk_usage.model_dump()
                         if hasattr(chunk_usage, "model_dump")
                         else dict(chunk_usage))
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason or finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            # Thinking models stream hidden reasoning on its own channel
            # (DeepSeek's ``reasoning_content``). Count it: an empty answer
            # that spent its budget thinking is explainable, one with no
            # evidence at all is not. Never merge it into the reply — thinking
            # is not an answer — but do not drop it without trace either, or
            # the caller cannot tell "said nothing" from "thought hard and
            # said nothing". Hand each increment to the consumer as its own
            # typed envelope (never mixed with the content deltas below) so the
            # UI can show a live thinking line.
            reasoning = _reasoning_field(delta)
            if reasoning:
                reasoning_chars += len(reasoning)
                reasoning_accum += reasoning
                yield json.dumps({"type": "reasoning", "text": reasoning})
            if delta.content:
                content_chars += len(delta.content)
                yield delta.content
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_by_index:
                        tool_calls_by_index[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tool_calls_by_index[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_by_index[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_by_index[idx]["arguments"] += tc_delta.function.arguments
        # Yield accumulated tool_calls as JSON
        if tool_calls_by_index:
            calls = []
            for idx in sorted(tool_calls_by_index):
                tc = tool_calls_by_index[idx]
                try:
                    args = json.loads(tc["arguments"])
                except (ValueError, TypeError):
                    args = tc["arguments"]
                calls.append({"id": tc["id"], "name": tc["name"], "arguments": args})
            yield json.dumps({"type": "tool_calls", "tool_calls": calls})
        # Streaming fallback for the same local-model shape as ``complete()``:
        # the model streamed its whole answer on the reasoning channel and not a
        # single content delta. Only when there was no content AND no tool call
        # (a tool call is a legitimate empty-content turn) do we surface the
        # reasoning as the reply — otherwise the turn reaches the caller blank
        # and the UI shows "模型没有返回任何内容". It is emitted as an ordinary
        # text delta so every downstream consumer treats it as the reply.
        reply_from_reasoning = False
        if content_chars == 0 and not tool_calls_by_index and reasoning_accum.strip():
            reply_from_reasoning = True
            logger.warning(
                "streamed reply had no content and %d chars of reasoning; "
                "using the reasoning channel as the reply (model=%s)",
                reasoning_chars, self.config.model,
            )
            yield reasoning_accum
        # Trailing metadata envelope. The stream contract is "plain text
        # deltas plus typed JSON envelopes"; the consumer recognises the
        # ``type`` and never renders this as text.
        yield json.dumps({
            "type": "stream_meta",
            "finish_reason": finish_reason,
            "reasoning_chars": reasoning_chars,
            "reply_from_reasoning": reply_from_reasoning,
            "usage": usage,
        })

    async def close(self):
        await self._client.close()

# Register for all OpenAI-compatible providers
for name in ["openai", "openrouter", "deepseek", "dashscope", "zhipuai", "fireworks", "siliconflow"]:
    ProviderRegistry.register(name, OpenAIProvider)
