"""Mixin AgentToolMixin — split from kairos/agents/base.py."""
from __future__ import annotations
import asyncio
import json
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.errors import is_context_length_error
from kairos.llm.provider_registry import create_provider
from kairos.context_governor import (
    DEFAULT_KEEP_RECENT_TOOL_RESULTS,
    elide_old_tool_results,
    shrink_for_overflow,
)
from kairos.core.message_bus import Message, MessageBus
from kairos.sentinel import get_sentinel
from kairos.taint import (TaintTracker, classify, current_tracker, mcp_server_of,
                          release_tracker, use_tracker)
from kairos.tools.base import ToolResult
from kairos.voice_text import VOICE_REPLY_DIRECTIVE

logger = logging.getLogger(__name__)




class AgentToolMixin:
    async def _dispatch_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call and return the result."""
        return await self._dispatch_tool_with_args(tool_call, None)

    async def _handle_write_todos(
        self, arguments: Dict[str, Any],
        task: "AgentTask", turn: int,
    ) -> ToolResult:
        """Apply a ``write_todos`` tool call to ``self.plan_tracker``.

        Round 11 wiring. The LLM emits a fresh plan as a tool call
        (Anthropic / DeepAgents pattern); we diff it against the
        current plan and publish the diff on the bus so the UI
        can render a live "Current plan" panel.

        Returns a synthetic ``ToolResult`` so the agent's tool loop
        continues without a real tool being invoked.
        """
        from kairos.loop.plan import apply_write_todos
        from kairos.tools.base import ToolResult
        try:
            diffs = apply_write_todos(self.plan_tracker, arguments or {})
        except Exception as exc:  # malformed input — surface as tool error
            logger.debug("write_todos apply failed: %s", exc)
            return ToolResult(
                success=False,
                output="",
                error=f"write_todos failed: {exc}",
            )
        # `apply_write_todos` returns a list of diff strings; if
        # the only entry starts with "write_todos: invalid", the
        # input was malformed and the apply was a no-op.
        if diffs and len(diffs) == 1 and diffs[0].startswith("write_todos: invalid"):
            return ToolResult(
                success=False,
                output="",
                error=diffs[0],
            )
        diff_text = "\n".join(diffs) if diffs else "(no changes)"
        # Publish on the bus so the UI can update its plan panel.
        try:
            await self.message_bus.publish(Message(
                sender=self.agent_id,
                topic="plan.updated",
                content=diff_text,
                msg_type="text",
                metadata={
                    "task_id": task.id,
                    "turn": turn + 1,
                    "plan": self.plan_tracker.to_dict(),
                },
            ))
        except Exception:
            logger.debug("plan.updated publish failed (non-fatal)",
                          exc_info=True)
        return ToolResult(
            success=True,
            output=(
                f"Plan updated. Current state:\n"
                f"{self._render_plan_inline()}"
            ),
        )

    def _render_plan_inline(self) -> str:
        """Compact inline rendering of the current plan, used in
        the synthetic tool output so the model can see its own plan
        state in subsequent turns.
        """
        try:
            from kairos.loop.plan import render_plan_block
            return render_plan_block(self.plan_tracker) or "(empty plan)"
        except Exception:
            return "(plan render failed)"

    def refresh_model(self, llm_config: LLMConfig):
        """Swap this agent's LLM provider safely.

        Builds the new provider BEFORE mutating state, so a construction
        failure leaves the agent usable and a concurrent reader never sees a
        new config paired with an old provider.

        NOTE: we deliberately do NOT close the previous provider here. It may
        still be referenced by ``ModelRouter._provider_cache`` (providers are
        cached per role) or by another agent, so closing it would break later
        calls with "client has been closed". The old provider is left for GC —
        same as before this method was hardened.
        """
        new_llm = create_provider(llm_config)
        self._llm_config = llm_config
        self._llm = new_llm

    def fork(self) -> "KairosAgent":
        """Return a parallel-worker copy of this agent.

        Shares the LLM client, tools, message bus, and checkpointer, but gets
        an ISOLATED copy of ``_memory`` (and ``_memory_summary``) plus a fresh
        ``_lock``. This lets best-of-N / team workers run *truly concurrently*
        without one attempt's tool/message history leaking into another.

        The copy keeps the same ``agent_id`` / ``role`` / ``model``; only
        mutable per-run state is reset.
        """
        import copy

        clone = copy.copy(self)
        # Isolate the mutable per-run state. The baseline ``LLMMessage``
        # objects are appended-to-in-place (run() only appends new ones), so
        # a shallow list copy gives each worker its own history view.
        clone._memory = list(self._memory)
        clone._memory_summary = self._memory_summary
        clone._last_summarized_at_turn = self._last_summarized_at_turn
        clone._lock = asyncio.Lock()
        clone.status = type(self.status).IDLE
        clone.current_turn = 0
        clone.current_tool = None
        clone.current_task = None
        clone.total_turns = 0
        return clone
