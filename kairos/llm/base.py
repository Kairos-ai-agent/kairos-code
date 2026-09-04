"""Base LLM Provider interface."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, List, Optional

from pydantic import BaseModel, field_validator

class ToolCall(BaseModel):
    """A tool call from the LLM."""

    id: str = ""
    name: str = ""
    arguments: Any = ""  # str or dict

class LLMMessage(BaseModel):
    """A single message in a conversation."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None

class LLMConfig(BaseModel):
    """Configuration for an LLM provider."""

    provider: str
    model: str
    api_key: str = ""
    base_url: Optional[str] = None
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: int = 120

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, v: Optional[str]) -> Optional[str]:
        """Trim a base_url so the provider's own "/chat/completions"
        (openai) or "/v1/messages" (anthropic) append never doubles the
        path.

        Example upstream bug: a frontend derived ".../v1/chat" from
        ".../v1/chat/completions"; the OpenAI SDK then appended
        "/chat/completions" → ".../v1/chat/chat/completions" (404). We
        normalize to the API root here so every provider reads a clean
        base_url regardless of caller.
        """
        if not v:
            return v
        base = v.rstrip("/")
        base = re.sub(r"/v1/chat/completions$", "", base, flags=re.IGNORECASE)
        base = re.sub(r"/chat/completions$", "", base, flags=re.IGNORECASE)
        base = re.sub(r"/v1/chat$", "", base, flags=re.IGNORECASE)
        base = re.sub(r"/chat$", "", base, flags=re.IGNORECASE)
        base = re.sub(r"/v1/messages$", "", base, flags=re.IGNORECASE)
        base = re.sub(r"/messages$", "", base, flags=re.IGNORECASE)
        return base or v.rstrip("/")

class LLMResponse(BaseModel):
    """Response from an LLM provider."""

    content: str
    model: str
    usage: dict = {}
    finish_reason: str = ""
    tool_calls: Optional[List[ToolCall]] = None

class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def complete(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Send a completion request and return the response."""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream completion tokens."""
        ...

    async def close(self):
        """Clean up resources."""
        pass
