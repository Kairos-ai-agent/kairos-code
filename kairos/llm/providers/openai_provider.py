"""OpenAI LLM Provider (also compatible with OpenRouter, DeepSeek, etc.)."""

from __future__ import annotations

import json
import os
import re
from typing import AsyncIterator, List, Optional

from kairos.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.provider_registry import ProviderRegistry
from kairos.llm.providers.base import format_messages_for_openai


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

        kwargs = {"api_key": config.api_key or "sk-placeholder", "max_retries": 2}
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
            # Enhanced error handling for better diagnostics
            if "text/plain" in str(e).lower() or "not json" in str(e).lower():
                raise type(e)(f"API returned text/plain instead of JSON. Check base_url and model availability: {e}") from e
            raise
        
        choice = response.choices[0]

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
            content=choice.message.content or "",
            model=response.model,
            usage=response.usage.model_dump() if response.usage else {},
            finish_reason=choice.finish_reason or "",
            tool_calls=tool_calls,
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
        response = await self._client.chat.completions.create(**kwargs)
        # Accumulate tool_call deltas
        tool_calls_by_index: dict = {}
        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
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

    async def close(self):
        await self._client.close()

# Register for all OpenAI-compatible providers
for name in ["openai", "openrouter", "deepseek", "dashscope", "zhipuai", "fireworks", "siliconflow"]:
    ProviderRegistry.register(name, OpenAIProvider)
