"""Base LLM Provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, List, Optional

from pydantic import BaseModel


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
