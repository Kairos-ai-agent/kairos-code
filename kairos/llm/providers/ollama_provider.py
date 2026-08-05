"""Ollama Local LLM Provider."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, List, Optional

import httpx

from kairos.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse
from kairos.llm.provider_registry import ProviderRegistry


class OllamaProvider(BaseLLMProvider):
    """Ollama local model provider via HTTP API."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self._base_url = config.base_url or "http://localhost:11434"
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=config.timeout)

    async def complete(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        payload = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "temperature": temperature or self.config.temperature,
                "num_predict": max_tokens or self.config.max_tokens,
            },
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        last_err = None
        for _attempt in range(3):
            try:
                response = await self._client.post("/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                break
            except Exception as e:
                last_err = e
                if _attempt < 2:
                    await asyncio.sleep(1 * (_attempt + 1))
        else:
            raise last_err

        # Parse tool calls
        tool_calls = None
        message = data.get("message", {})
        if message.get("tool_calls"):
            from kairos.llm.base import ToolCall
            tool_calls = []
            for i, tc in enumerate(message["tool_calls"]):
                func = tc.get("function", {})
                tool_calls.append(ToolCall(
                    id=tc.get("id", "") or f"ollama_call_{i}",
                    name=func.get("name", ""),
                    arguments=func.get("arguments", {}),
                ))

        return LLMResponse(
            content=message.get("content", ""),
            model=self.config.model,
            usage={
                "prompt_eval_count": data.get("prompt_eval_count", 0),
                "eval_count": data.get("eval_count", 0),
            },
            finish_reason="stop",
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {
                "temperature": temperature or self.config.temperature,
                "num_predict": max_tokens or self.config.max_tokens,
            },
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        # Accumulate tool_calls from streaming chunks
        tool_calls_by_index: dict = {}
        async with self._client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    data = json.loads(line)
                    message = data.get("message", {})
                    if message.get("content"):
                        yield message["content"]
                    for tc in message.get("tool_calls", []):
                        idx = tc.get("index", len(tool_calls_by_index))
                        func = tc.get("function", {})
                        tool_calls_by_index[idx] = {
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "arguments": func.get("arguments", {}),
                        }
        # Yield accumulated tool_calls as JSON
        if tool_calls_by_index:
            calls = [{"id": v["id"], "name": v["name"], "arguments": v["arguments"]}
                     for _, v in sorted(tool_calls_by_index.items())]
            yield json.dumps({"type": "tool_calls", "tool_calls": calls})

    async def close(self):
        await self._client.aclose()


ProviderRegistry.register("ollama", OllamaProvider)
