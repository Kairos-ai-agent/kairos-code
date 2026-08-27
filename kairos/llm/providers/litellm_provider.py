"""LiteLLM-backed provider.

Wraps the open-source ``litellm`` library so Kairos can talk to 100+
LLM providers (OpenAI, Anthropic, Bedrock, Vertex, Ollama, vLLM,
OpenRouter, Together, Groq, Mistral, Cohere, ...) through a single
class. The model string is the standard litellm form, e.g.:

    "gpt-4o"
    "anthropic/claude-3-5-sonnet-20241022"
    "bedrock/anthropic.claude-3-sonnet-20240229-v1:0"
    "ollama/qwen2.5-coder:7b"
    "openrouter/anthropic/claude-3.5-sonnet"
    "vllm/meta-llama/Llama-3.1-70B-Instruct"

Install:

    pip install litellm

The provider is registered as ``litellm`` in the global
``ProviderRegistry`` and is the recommended default for any new
integration. The legacy ``openai`` / ``anthropic`` / ``ollama``
classes are kept for the explicit cases where the user wants
zero extra dependencies.

Round 10 addition. Wraps streaming + tool calls + usage tokens
through the standard ``BaseLLMProvider`` interface so the rest of
Kairos (router, observability, memory) is provider-agnostic.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from kairos.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from kairos.llm.provider_registry import ProviderRegistry

logger = logging.getLogger(__name__)

# Lazy import — litellm is an optional dep. Importing it adds ~10MB and
# the calver pin can shift; we want Kairos to start even if it's missing
# and surface a clear error at first call.
try:
    import litellm  # type: ignore
    # Drop noisy INFO logs from litellm's underlying SDKs
    litellm.suppress_debug_info = True
    _HAS_LITELLM = True
except ImportError:  # pragma: no cover - import guard
    _HAS_LITELLM = False
    litellm = None  # type: ignore


def _ensure_litellm() -> None:
    if not _HAS_LITELLM:
        raise RuntimeError(
            "The 'litellm' package is not installed. "
            "Install it with: pip install litellm"
        )


def _coerce_messages(messages: List[LLMMessage]) -> List[Dict[str, Any]]:
    """Map our LLMMessage dataclass to litellm's dict format."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        d: Dict[str, Any] = {"role": m.role, "content": m.content}
        if m.name:
            d["name"] = m.name
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": (
                            json.dumps(tc.arguments)
                            if not isinstance(tc.arguments, str)
                            else tc.arguments
                        ),
                    },
                }
                for tc in m.tool_calls
            ]
        out.append(d)
    return out


def _coerce_tools(tools: Optional[List[dict]]) -> Optional[List[dict]]:
    """litellm expects the same OpenAI-style tool dicts we already
    produce; pass through."""
    return tools


class LiteLLMProvider(BaseLLMProvider):
    """Unified provider that talks to 100+ LLM backends via litellm.

    The model string in ``LLMConfig.model`` is interpreted by litellm:
    bare names like ``gpt-4o`` or ``claude-3-5-sonnet-20241022`` are
    resolved to the appropriate provider; prefixed names like
    ``bedrock/<id>`` or ``openrouter/<id>`` are routed explicitly.

    Notable litellm features this enables:
      - Automatic retry + exponential backoff on 429/5xx
      - Provider fallback chains (configured at the litellm level
        via ``Router``; here we just expose a single primary)
      - Response caching via ``caching=True`` (Redis / in-memory)
      - Cost tracking (litellm ships a per-model price table)
    """

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        _ensure_litellm()
        # Optional API key from the model config (for backends that
        # don't read env vars automatically). Most users wire keys
        # via env; this is the override path.
        self._api_key = config.api_key or os.environ.get("LITELLM_API_KEY")
        # Base URL is honored for the OpenAI-compatible transport
        # (vllm, ollama, openrouter proxies, self-hosted gateways).
        self._base_url = config.base_url

    @property
    def name(self) -> str:
        return "litellm"

    @property
    def model(self) -> str:
        return self.config.model

    def _completion_kwargs(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool,
    ) -> Dict[str, Any]:
        kw: Dict[str, Any] = {
            "model": self.config.model,
            "messages": _coerce_messages(messages),
            "stream": stream,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        if tools:
            kw["tools"] = _coerce_tools(tools)
            kw["tool_choice"] = "auto"
        if self._api_key:
            kw["api_key"] = self._api_key
        if self._base_url:
            kw["api_base"] = self._base_url
        # num_retries falls back to litellm's default (3)
        return kw

    async def complete(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        _ensure_litellm()
        kw = self._completion_kwargs(
            messages, tools, temperature, max_tokens, stream=False
        )
        try:
            resp = await litellm.acompletion(**kw)
        except Exception as exc:
            logger.warning("litellm completion failed: %s", exc)
            raise
        return _to_response(resp)

    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        _ensure_litellm()
        kw = self._completion_kwargs(
            messages, tools, temperature, max_tokens, stream=True
        )
        async for chunk in await litellm.acompletion(**kw):
            # litellm stream chunks are typed objects with .choices[0].delta
            try:
                delta = chunk.choices[0].delta
            except (AttributeError, IndexError):
                continue
            # Tool-call JSON sentinel: litellm emits incremental tool_call
            # deltas; we collapse them into a final sentinel at the
            # end. Until then, just yield the visible text.
            text = getattr(delta, "content", None) if delta else None
            if text:
                yield text
            # If the chunk carries a finish_reason of "tool_calls" with
            # a complete tool_calls payload, emit a final sentinel so
            # the existing _stream_complete parser in kairos.agents.base
            # can pick it up.
            try:
                finish = chunk.choices[0].finish_reason
            except (AttributeError, IndexError):
                finish = None
            if finish == "tool_calls":
                # Final accumulated tool_calls
                tool_calls = getattr(delta, "tool_calls", None) or []
                if tool_calls:
                    calls = []
                    for tc in tool_calls:
                        func = getattr(tc, "function", None) or {}
                        args = func.get("arguments", "") or ""
                        # Try to parse as JSON
                        try:
                            args_obj = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args_obj = args
                        calls.append({
                            "id": getattr(tc, "id", "") or "",
                            "name": (func.get("name", "") if isinstance(func, dict) else getattr(func, "name", "")) or "",
                            "arguments": args_obj,
                        })
                    yield json.dumps({"type": "tool_calls", "tool_calls": calls})


def _to_response(resp: Any) -> LLMResponse:
    """Map a litellm ModelResponse to our LLMResponse."""
    # Coerce model name to a real string — litellm sometimes returns
    # the route name as bytes/Enum and Pydantic is strict.
    model_name = str(getattr(resp, "model", "unknown") or "unknown")
    try:
        choice = resp.choices[0]
    except (AttributeError, IndexError, TypeError):
        return LLMResponse(
            content="", model=model_name, usage={}, finish_reason="error"
        )
    msg = getattr(choice, "message", None)
    content = (getattr(msg, "content", "") or "") if msg is not None else ""
    # Tool calls
    from kairos.llm.base import ToolCall
    tool_calls: List[ToolCall] = []
    raw_calls = getattr(msg, "tool_calls", None) if msg is not None else None
    if raw_calls:
        for tc in raw_calls:
            func = getattr(tc, "function", None) or {}
            if isinstance(func, dict):
                fname = str(func.get("name", "") or "")
                args = func.get("arguments", "")
            else:
                fname = str(getattr(func, "name", "") or "")
                args = getattr(func, "arguments", "")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    pass
            tool_calls.append(ToolCall(
                id=str(getattr(tc, "id", "") or ""),
                name=fname,
                arguments=args if args is not None else "",
            ))
    usage: Dict[str, int] = {}
    u = getattr(resp, "usage", None)
    if u is not None:
        usage = {
            "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
        }
    return LLMResponse(
        content=content or "",
        model=model_name,
        usage=usage,
        finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        tool_calls=tool_calls or None,
    )


# Register the provider under a stable name.
ProviderRegistry.register("litellm", LiteLLMProvider)
