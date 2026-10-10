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
    # The model's real context window, when we know it (settings, env, or a
    # provider that told us). Compaction has to fire *before* the request is
    # rejected, and a fixed budget cannot do that for a window it has never
    # been told about. None = unknown, keep the agent's own budget.
    context_window: Optional[int] = None
    # Hard ceiling on the PROMPT (system + history + tool schemas) sent in one
    # request. A local model with a small window (e.g. LM Studio's 8192) rejects
    # the whole call with "request (8208 tokens) exceeds the available context
    # size" the moment the assembled prompt crosses it. When set, the request is
    # trimmed to fit BEFORE it is sent (see kairos.context_governor.fit_to_budget);
    # None = unchanged (the agent's own budget governs).
    max_prompt_tokens: Optional[int] = None
    # Local models are overwhelmed by the full tool catalogue (~22 schemas is
    # the single biggest chunk of a small prompt) and almost never call any of
    # them. True registers only the core file/terminal/grep tools. None/False =
    # unchanged (every tool the agent was wired with is advertised).
    light_tools: Optional[bool] = None
    # Per-call wall-clock timeout in seconds. None = the historical default
    # (``timeout or 60``); 0 or negative = no limit (a slow local model may
    # legitimately take many minutes on one turn); positive = that many seconds.
    # Kept separate from ``timeout`` so a config written before this existed
    # keeps its exact behaviour.
    timeout_s: Optional[int] = None

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
    # Characters a thinking model streamed on its hidden-reasoning channel
    # (DeepSeek's ``reasoning_content``). Deliberately a count, not the text:
    # it exists so an empty reply can be explained, not to be replayed.
    reasoning_chars: int = 0
    # The *tail* of that hidden channel (never the whole thing — one turn can
    # reason for ~24k characters). Carried only so a NON-streaming caller
    # (``chat()`` uses ``complete()``) can publish an ``agent.thinking`` hint
    # and the UI can show a live line. It must never be merged into
    # ``content``, which is the answer and only the answer.
    reasoning_tail: str = ""
    # Set ONLY when the provider saw an empty ``content`` and a non-empty
    # hidden-reasoning channel (a thinking model that reasoned but did not
    # answer). ``content`` is left EMPTY in that case -- the reasoning is never
    # promoted into it (the answer channel carries the answer and only the
    # answer). This flag is the decidable telemetry that lets a caller retry,
    # show a readable notice, or nudge the model, instead of silently passing
    # the model's private monologue off as the reply. False on every normal
    # reply, and on an empty-content turn that carried a tool call.
    reply_from_reasoning: bool = False

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
