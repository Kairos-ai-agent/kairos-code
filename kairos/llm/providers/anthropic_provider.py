"""Anthropic-compatible LLM Provider (supports MiniMax, Claude, etc.)."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, List, Optional

import httpx

from kairos.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.provider_registry import ProviderRegistry

class AnthropicProvider(BaseLLMProvider):
    """Anthropic-compatible provider using direct HTTP calls."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        # Determine base URL
        if config.base_url:
            self._base_url = config.base_url.rstrip("/")
        else:
            self._base_url = "https://api.anthropic.com"
        self._client = httpx.AsyncClient(
            timeout=config.timeout or 60,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def _build_headers(self) -> dict:
        return {
            "x-api-key": self.config.api_key or "sk-placeholder",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _convert_messages(self, messages: List[LLMMessage]):
        system = ""
        converted = []
        for msg in messages:
            if msg.role == "system":
                system = msg.content
            elif msg.role == "tool":
                # Anthropic uses "user" role for tool results
                tool_use_id = msg.tool_call_id
                if not tool_use_id:
                    # Generate fallback id based on message order
                    tool_use_id = f"toolu_fallback_{len(converted)}"
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": msg.content,
                    }],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                # Convert tool_calls to Anthropic tool_use blocks
                content_blocks = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    args = tc.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": args if isinstance(args, dict) else {},
                    })
                converted.append({"role": "assistant", "content": content_blocks})
            else:
                converted.append({"role": msg.role, "content": msg.content})
        return system, converted

    async def complete(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        system, converted = self._convert_messages(messages)
        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "messages": converted,
        }
        if system:
            # Cache the system prompt across rounds (Anthropic
            # prompt caching: 1.25x write, 0.1x read). Saves
            # ~50% on loops where the system prompt is large.
            payload["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
        if temperature is not None:
            payload["temperature"] = temperature
        elif self.config.temperature is not None:
            payload["temperature"] = self.config.temperature

        # Add tools (Anthropic format) with cache_control on the last one.
        # Caches the entire tools array across rounds (~30% extra cost
        # first call, then 0.1x). Major savings on long loops.
        if tools:
            payload["tools"] = [{
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
            } for t in tools]
            if payload["tools"]:
                payload["tools"][-1]["cache_control"] = {"type": "ephemeral"}

        url = f"{self._base_url}/v1/messages"
        last_err = None
        for _attempt in range(3):
            try:
                resp = await self._client.post(url, json=payload, headers=self._build_headers())
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                last_err = e
                if _attempt < 2:
                    await asyncio.sleep(1 * (_attempt + 1))
        else:
            raise last_err

        content = ""
        tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=data.get("model", self.config.model),
            usage={
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
            finish_reason=data.get("stop_reason", ""),
            tool_calls=tool_calls if tool_calls else None,
        )

    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        system, converted = self._convert_messages(messages)
        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "messages": converted,
            "stream": True,
        }
        if system:
            # Cache the system prompt across rounds (Anthropic
            # prompt caching: 1.25x write, 0.1x read). Saves
            # ~50% on loops where the system prompt is large.
            payload["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = [{
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
            } for t in tools]

        url = f"{self._base_url}/v1/messages"
        # Accumulate tool_use blocks from streaming
        tool_blocks: dict = {}
        async with self._client.stream("POST", url, json=payload, headers=self._build_headers()) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    event_type = data.get("type", "")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("text"):
                            yield delta["text"]
                        if delta.get("type") == "input_json_delta":
                            idx = data.get("index", 0)
                            if idx not in tool_blocks:
                                tool_blocks[idx] = {"id": "", "name": "", "input": ""}
                            tool_blocks[idx]["input"] += delta.get("partial_json", "")
                    elif event_type == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            idx = data.get("index", 0)
                            tool_blocks[idx] = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": "",
                            }
        # Yield accumulated tool_calls as JSON
        if tool_blocks:
            calls = []
            for idx in sorted(tool_blocks):
                tb = tool_blocks[idx]
                try:
                    args = json.loads(tb["input"])
                except (ValueError, TypeError):
                    args = tb["input"]
                calls.append({"id": tb["id"], "name": tb["name"], "arguments": args})
            yield json.dumps({"type": "tool_calls", "tool_calls": calls})

    async def close(self):
        await self._client.aclose()

ProviderRegistry.register("anthropic", AnthropicProvider)
ProviderRegistry.register("claude", AnthropicProvider)
