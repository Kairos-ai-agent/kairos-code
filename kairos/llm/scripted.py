"""A scripted LLM provider — deterministic, offline, zero-key.

Why this exists: the product's pitch is the *gate* (the review loop and its
ledger), and a new user cannot see the gate until they have installed the
project, obtained an API key, and waited for a real model. ``kairos demo``
removes all three by wiring the **real** LoopReview engine to a script that
plays one Coder round per round, so the gate, the score curve, the rejections
and the cost ledger are all visible in seconds without a key or a network.

This is deliberately not a mock of the loop. The loop is real; only the model is
scripted, and every tool call the script emits runs through the real sandboxed
tools — files really get written, tests really get executed, and the reviewer's
verdict still goes through the same calibrate/normalise pipeline as a live run.

``stream()`` is implemented faithfully (content deltas plus the OpenAI-style
final ``{"type":"tool_calls"}`` sentinel) so the agent's streaming path behaves
exactly as it does with a real provider.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from kairos.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMMessage,
    LLMResponse,
    ToolCall,
)

#: A script maps (conversation, call number, tool schemas) → the next response.
#: ``tools`` is empty/None when the caller hid tools (e.g. plan mode), which a
#: script can use as "answer in prose, do not call a tool".
Script = Callable[..., LLMResponse]


def last_role(messages: List[LLMMessage]) -> str:
    """Role of the final message (``""`` when the conversation is empty)."""
    return messages[-1].role if messages else ""


def conversation_text(messages: List[LLMMessage]) -> str:
    """Flatten the conversation for scripts that key off prompt text."""
    return "\n".join((m.content or "") for m in messages)


def text_response(content: str, model: str = "scripted") -> LLMResponse:
    return LLMResponse(content=content, model=model, finish_reason="stop")


def tool_response(calls: List[ToolCall], content: str = "", model: str = "scripted") -> LLMResponse:
    return LLMResponse(content=content, model=model, finish_reason="tool_calls",
                       tool_calls=calls)


def write_file_call(path: str, content: str, call_id: str = "call_write") -> ToolCall:
    return ToolCall(id=call_id, name="file_write",
                    arguments={"path": path, "content": content})


def terminal_call(command: str, call_id: str = "call_terminal") -> ToolCall:
    return ToolCall(id=call_id, name="terminal", arguments={"command": command})


class ScriptedProvider(BaseLLMProvider):
    """A provider whose answers come from a Python function, not a model."""

    def __init__(self, config: LLMConfig, script: Script, *, label: str = "scripted",
                 record_costs: bool = True):
        super().__init__(config)
        self._script = script
        self.label = label
        self.record_costs = record_costs
        self.calls = 0
        self.transcript: List[Dict[str, Any]] = []

    async def complete(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        self.calls += 1
        response = self._script(list(messages), self.calls, tools)
        if not response.model:
            response.model = self.config.model
        # Rough, clearly-labelled token accounting: the ledger should render the
        # calls even though a scripted run costs nothing.
        prompt_chars = sum(len(m.content or "") for m in messages)
        usage = dict(response.usage or {})
        usage.setdefault("prompt_tokens", max(1, prompt_chars // 4))
        usage.setdefault("completion_tokens", max(1, len(response.content or "") // 4))
        usage.setdefault("total_tokens",
                         usage["prompt_tokens"] + usage["completion_tokens"])
        response.usage = usage
        self.transcript.append({
            "call": self.calls,
            "last_role": last_role(messages),
            "tool_calls": [tc.name for tc in (response.tool_calls or [])],
            "content_chars": len(response.content or ""),
        })
        if self.record_costs:
            self._record_cost(usage)
        return response

    def _record_cost(self, usage: Dict[str, Any]) -> None:
        """Put this call in the cost ledger.

        The amount is genuinely 0.0 (a script bills nothing), but recording it
        keeps the ledger honest about *what ran*: the Gate Report then shows the
        calls and the $0.000000 next to them instead of an empty table.
        """
        try:
            from kairos import cost as cost_mod
            cost_mod.record_entry(
                self.config.model,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cost_usd=0.0,
                provider="scripted",
                duration_ms=0,
                call_id=f"{self.label}-{self.calls}-{uuid.uuid4().hex[:6]}",
            )
        except Exception:
            pass

    async def stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """Yield content, then the OpenAI-style tool-call sentinel line."""
        response = await self.complete(messages, tools=tools, temperature=temperature)
        if response.content:
            yield response.content
        if response.tool_calls:
            payload = []
            for call in response.tool_calls:
                args = call.arguments
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                payload.append({"id": call.id, "name": call.name, "arguments": args})
            yield json.dumps({"type": "tool_calls", "tool_calls": payload})

    async def close(self) -> None:  # pragma: no cover - nothing to release
        return None
