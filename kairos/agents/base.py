"""KairosAgent - Base class for all agents in the system."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from kairos.llm.base import LLMConfig, LLMMessage, LLMResponse, ToolCall
from kairos.llm.provider_registry import create_provider
from kairos.core.message_bus import Message, MessageBus
from kairos.tools.base import ToolResult

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    ERROR = "error"


class AgentTask(BaseModel):
    """A task assigned to an agent."""

    id: str
    title: str
    description: str
    context: Dict[str, Any] = {}
    status: str = "pending"
    result: Optional[str] = None


class AgentState(BaseModel):
    """Observable state of an agent for the UI."""

    agent_id: str
    name: str
    role: str
    status: str = "idle"
    current_task: Optional[str] = None
    model: str = ""
    last_activity: float = 0.0
    message_count: int = 0
    # Granular progress so the UI can show "Turn 3/8 — using file_write"
    # without needing to parse message history.
    current_turn: int = 0
    total_turns: int = 0
    current_tool: Optional[str] = None


class KairosAgent:
    """Base agent with tool-calling loop.

    Each agent has:
    - A role (PM, Architect, Engineer, etc.)
    - An LLM provider (configurable per agent)
    - Access to tools (file edit, terminal, search, etc.)
    - Memory (conversation history)
    - Message bus connection for inter-agent communication
    """

    # Per-task turn budgets. Shorter than the old 15 to surface stuck
    # agents earlier — agents with more than 8 tool rounds are usually
    # looping and a fresh attempt tends to do better than another turn.
    MAX_TOOL_TURNS = 8

    # Per-call temperature. None = use the provider default. The Coder
    # loop decays this across rounds via set_temperature(); other agents
    # (Reviewer, specialists) leave it alone.
    temperature: Optional[float] = None
    MAX_CHAT_TURNS = 5

    # Per-LLM-call timeout (seconds). Pulled from the provider's LLMConfig
    # and applied with asyncio.wait_for so a hung provider can't tie up an
    # entire dispatch. The dispatch-level DISPATCH_TIMEOUT_S (orchestrator)
    # remains the hard outer bound.
    @property
    def _llm_timeout_s(self) -> float:
        return float(self._llm_config.timeout or 60)

    async def _stream_complete(self, messages, tools, task, turn_no: int):
        """Call LLM with streaming. Each content delta is published as a
        `stream.chunk` event so the UI can render a typewriter effect.

        Falls back to non-streaming `complete()` if the provider raises
        any streaming-specific error — better a working non-stream than a
        crashed tool call.
        """
        accumulated_text = ""
        accumulated_tool_calls = []
        finish_reason = ""
        model_name = self._llm_config.model
        usage = {}
        chunk_seq = 0

        try:
            stream_ctx = self._llm.stream(messages, tools=tools, temperature=self.temperature)
        except Exception as e:
            logger.debug("stream() unavailable, falling back to complete(): %s", e)
            return await self._llm.complete(messages, tools=tools, temperature=self.temperature)

        # Iterate chunks. The provider's stream() is an AsyncIterator[str]
        # — OpenAI emits raw delta strings AND a final sentinel
        # '{"type":"tool_calls", "tool_calls":[...]}' line that we have to
        # parse out. Other providers may differ; we tolerate either.
        try:
            async for chunk in stream_ctx:
                if isinstance(chunk, str) and chunk.startswith("{"):
                    # Final tool_calls payload from OpenAI streaming.
                    try:
                        parsed = json.loads(chunk)
                        if isinstance(parsed, dict) and parsed.get("type") == "tool_calls":
                            for tc in parsed.get("tool_calls") or []:
                                args = tc.get("arguments")
                                if isinstance(args, str):
                                    try:
                                        args = json.loads(args)
                                    except (json.JSONDecodeError, TypeError):
                                        pass
                                accumulated_tool_calls.append(ToolCall(
                                    id=tc.get("id", ""),
                                    name=tc.get("name", ""),
                                    arguments=args if args is not None else "",
                                ))
                            continue
                    except (json.JSONDecodeError, ValueError):
                        pass
                # Plain text delta — emit as stream.chunk and accumulate.
                accumulated_text += chunk
                chunk_seq += 1
                # Publish every delta. Throttling is the UI's job.
                await self.message_bus.publish(Message(
                    sender=self.agent_id,
                    topic="stream.chunk",
                    content=chunk,
                    msg_type="text",
                    metadata={
                        "task_id": task.id,
                        "turn": turn_no,
                        "seq": chunk_seq,
                        "accumulated_len": len(accumulated_text),
                    },
                ))
        except Exception as e:
            logger.debug("Stream interrupted mid-way (%s); falling back", e)
            # Fall back to non-stream so we still get a final answer.
            try:
                return await self._llm.complete(messages, tools=tools, temperature=self.temperature)
            except Exception:
                # Re-raise the original stream error if complete also fails.
                raise

        return LLMResponse(
            content=accumulated_text,
            model=model_name,
            usage=usage,
            finish_reason=finish_reason,
            tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
        )

    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        llm_config: LLMConfig,
        message_bus: MessageBus,
        tools: Optional[List[Any]] = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.message_bus = message_bus
        self.tools = tools or []

        # LLM
        self._llm = create_provider(llm_config)
        self._llm_config = llm_config

        # State
        self.status = AgentStatus.IDLE
        self.current_task: Optional[AgentTask] = None
        # Live progress fields (read by .state below and updated each turn
        # so the WS heartbeat can push them out to the UI).
        self.current_turn: int = 0
        self.total_turns: int = 0
        self.current_tool: Optional[str] = None

        # Concurrency protection
        self._lock = asyncio.Lock()

        # Memory with token counting
        self._memory: List[LLMMessage] = []
        self._max_tokens = 80000  # Token budget
        self._keep_recent = 4     # Always keep last N messages

        # Message bus subscription
        self.message_bus.subscribe(agent_id, f"agent.{agent_id}")
        self.message_bus.subscribe(agent_id, "broadcast")

    @property
    def state(self) -> AgentState:
        return AgentState(
            agent_id=self.agent_id,
            name=self.name,
            role=self.role,
            status=self.status.value,
            current_task=self.current_task.title if self.current_task else None,
            model=self._llm_config.model,
            last_activity=time.time(),
            message_count=len(self._memory),
            current_turn=self.current_turn,
            total_turns=self.total_turns,
            current_tool=self.current_tool,
        )

    def refresh_model(self, llm_config: LLMConfig):
        self._llm_config = llm_config
        self._llm = create_provider(llm_config)

    def _get_tool_schemas(self) -> Optional[List[dict]]:
        """Get tool schemas for LLM function calling."""
        if not self.tools:
            return None
        schemas = []
        for tool in self.tools:
            schema = tool.to_schema()
            schemas.append({
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema.get("parameters", {
                    "type": "object",
                    "properties": {},
                }),
            })
        return schemas

    async def _dispatch_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call and return the result."""
        return await self._dispatch_tool_with_args(tool_call, None)

    async def _dispatch_tool_with_args(self, tool_call: ToolCall,
                                        override_args) -> ToolResult:
        """Like _dispatch_tool but `override_args` (a dict) replaces the
        tool call's parsed arguments. Used by the hook system to let
        pre_tool_use hooks rewrite tool calls."""
        for tool in self.tools:
            if tool.name == tool_call.name:
                if override_args is not None:
                    args = dict(override_args)
                else:
                    args = tool_call.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                try:
                    return await tool.execute(**args)
                except Exception as e:
                    return ToolResult(success=False, output="", error=str(e))
        return ToolResult(success=False, output="", error=f"Unknown tool: {tool_call.name}")

    def _count_tokens(self, text: str) -> int:
        """Estimate token count (rough: ~4 chars per token)."""
        return len(text) // 4 + 4  # +4 for role overhead

    def _truncate_memory(self):
        """Truncate memory to fit within token budget, keeping recent messages."""
        total = sum(self._count_tokens(m.content) for m in self._memory)
        while len(self._memory) > self._keep_recent and total > self._max_tokens:
            removed = self._memory.pop(0)
            total -= self._count_tokens(removed.content)

    def _build_messages(self) -> List[LLMMessage]:
        """Build messages for LLM including system prompt and memory."""
        self._truncate_memory()
        return (
            [LLMMessage(role="system", content=self.system_prompt)]
            + self._memory
        )

    async def run(self, task: AgentTask, plan_mode: bool = False) -> str:
        """Main agent loop with tool-calling support.

        Flow: user task -> LLM -> tool_calls? -> execute tools -> LLM -> ... -> final answer

        When `plan_mode=True`, tools are NOT exposed on the first turn —
        the agent must respond with a plan in prose. This is used by the
        orchestrator to force a "plan first, then execute" workflow that
        the user can approve before code starts changing.
        """
        async with self._lock:
            return await self._run_impl(task, plan_mode=plan_mode)

    async def _run_impl(self, task: AgentTask, plan_mode: bool = False) -> str:
        """Internal run implementation (called with lock held)."""
        self.current_task = task
        self.status = AgentStatus.THINKING
        self.current_turn = 0
        self.total_turns = self.MAX_TOOL_TURNS
        self.current_tool = None
        # Stash the project_id on the agent so hooks can reach it without
        # threading it through every call. Default "" for tests that
        # don't care about hooks.
        self._current_project_id = (task.context or {}).get("project_id", "")

        # Check API key
        if not self._llm_config.api_key or self._llm_config.api_key == "sk-placeholder":
            self.status = AgentStatus.ERROR
            error_msg = f"No API key for {self._llm_config.model}. Configure in Settings."
            task.status = "failed"
            task.result = error_msg
            return error_msg

        try:
            # Add task to memory
            user_message = LLMMessage(
                role="user",
                content=self._build_task_prompt(task),
            )
            self._memory.append(user_message)

            # Get tool schemas. In plan_mode, hide tools entirely on the
            # first turn so the agent can't skip planning and start
            # editing. The orchestrator runs plan-mode agents with a
            # plan_mode flag, captures the markdown plan, and asks the
            # user to approve before allowing real tool execution.
            tool_schemas = None if plan_mode else self._get_tool_schemas()

            # Tool-calling loop
            result = ""
            timed_out = False
            for turn in range(self.MAX_TOOL_TURNS):
                self.status = AgentStatus.THINKING
                self.current_turn = turn + 1
                self.current_tool = None
                # Announce this turn on the bus so the UI can show progress
                # even for agents without tools (e.g. Team Leader), whose
                # activity was previously invisible until the final result.
                await self.message_bus.publish(Message(
                    sender=self.agent_id,
                    topic="agent.thinking",
                    content=f"Turn {turn + 1}/{self.MAX_TOOL_TURNS}: reasoning...",
                    msg_type="text",
                    metadata={"task_id": task.id, "turn": turn + 1,
                              "total_turns": self.MAX_TOOL_TURNS},
                ))
                messages = self._build_messages()
                # Per-call timeout so a hung provider can't tie up the whole
                # dispatch. asyncio.TimeoutError surfaces as a clear failure
                # to the UI instead of an opaque 2-minute freeze.
                try:
                    response = await asyncio.wait_for(
                        self._stream_complete(messages, tool_schemas, task, turn + 1),
                        timeout=self._llm_timeout_s,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    result = (
                        f"LLM call timed out after {self._llm_timeout_s:.0f}s "
                        f"on turn {turn + 1}/{self.MAX_TOOL_TURNS}"
                    )
                    await self.message_bus.publish(Message(
                        sender=self.agent_id,
                        topic="task.error",
                        content=result,
                        msg_type="error",
                        metadata={"task_id": task.id, "turn": turn + 1,
                                  "reason": "llm_timeout"},
                    ))
                    break

                # Store assistant response in memory (always, even if content is empty but has tool_calls)
                self._memory.append(LLMMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                ))

                # Surface the LLM's interim text so the UI shows something
                # between "thinking" and the final result. Truncated so
                # we don't flood the message bus with a 50KB JSON plan.
                interim = (response.content or "").strip()
                if interim:
                    await self.message_bus.publish(Message(
                        sender=self.agent_id,
                        topic="agent.response",
                        content=interim[:500],
                        msg_type="text",
                        metadata={"task_id": task.id, "turn": turn + 1},
                    ))

                # No tool calls -> done
                if not response.tool_calls:
                    result = response.content
                    break

                # Execute tool calls
                self.status = AgentStatus.ACTING
                for tc in response.tool_calls:
                    self.current_tool = tc.name
                    # Publish tool call to bus
                    await self.message_bus.publish(Message(
                        sender=self.agent_id,
                        topic="tool.call",
                        content=f"Calling {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:200]})",
                        msg_type="text",
                        metadata={"task_id": task.id, "tool": tc.name,
                                  "turn": turn + 1},
                    ))

                    # Hooks: let user-defined pre_tool_use hooks see (and
                    # optionally rewrite) the arguments before they hit
                    # the tool. Default no-op if no hooks are installed.
                    from kairos.hooks import get_runner
                    tc_args = get_runner().pre_tool_use(
                        tool_name=tc.name,
                        arguments=tc.arguments if isinstance(tc.arguments, dict) else {},
                        agent_id=self.agent_id,
                        project_id=getattr(self, "_current_project_id", ""),
                    )

                    tool_result = await self._dispatch_tool_with_args(tc, tc_args)
                    self.current_tool = None

                    # Post-tool hooks (fire-and-forget).
                    try:
                        get_runner().post_tool_use(
                            tool_name=tc.name,
                            arguments=tc_args,
                            result=tool_result,
                            agent_id=self.agent_id,
                            project_id=getattr(self, "_current_project_id", ""),
                        )
                    except Exception:
                        logger.debug("post_tool_use hook dispatch failed",
                                     exc_info=True)

                    # Store tool result in memory
                    self._memory.append(LLMMessage(
                        role="tool",
                        content=tool_result.output if tool_result.success else f"Error: {tool_result.error}",
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))

                    # Publish tool result to bus
                    await self.message_bus.publish(Message(
                        sender=self.agent_id,
                        topic="tool.result",
                        content=f"{tc.name}: {'OK' if tool_result.success else tool_result.error}",
                        msg_type="text",
                        metadata={"task_id": task.id, "tool": tc.name,
                                  "success": tool_result.success, "turn": turn + 1},
                    ))
            else:
                result = f"Tool call limit reached after {self.MAX_TOOL_TURNS} turns."

            # Publish final result
            await self.message_bus.publish(Message(
                sender=self.agent_id,
                topic="task.result",
                content=result[:500],
                msg_type="result",
                metadata={"task_id": task.id},
            ))

            task.status = "completed" if not timed_out else "failed"
            task.result = result
            self.status = AgentStatus.IDLE
            self.current_turn = 0
            self.total_turns = 0
            self.current_tool = None
            return result

        except Exception as e:
            self.status = AgentStatus.ERROR
            task.status = "failed"
            task.result = str(e)
            await self.message_bus.publish(Message(
                sender=self.agent_id,
                topic="task.error",
                content=str(e),
                msg_type="error",
                metadata={"task_id": task.id},
            ))
            return f"Error: {e}"

        finally:
            self.current_task = None
            self.current_turn = 0
            self.total_turns = 0
            self.current_tool = None

    async def chat(self, message: str) -> str:
        """Direct chat with this agent (for UI interaction)."""
        async with self._lock:
            return await self._chat_impl(message)

    async def _chat_impl(self, message: str) -> str:
        """Internal chat implementation (called with lock held)."""
        user_msg = LLMMessage(role="user", content=message)
        self._memory.append(user_msg)

        tool_schemas = self._get_tool_schemas()

        # Use conversational prompt for chat mode (not task-specific JSON prompt)
        chat_system = "You are a helpful assistant. Respond conversationally to the user's message. Use tools when helpful."

        self.current_turn = 0
        self.total_turns = self.MAX_CHAT_TURNS
        last_response = None
        for turn in range(self.MAX_CHAT_TURNS):
            self.current_turn = turn + 1
            self._truncate_memory()
            messages = [LLMMessage(role="system", content=chat_system)] + self._memory
            try:
                response = await asyncio.wait_for(
                    self._llm.complete(messages, tools=tool_schemas, temperature=self.temperature),
                    timeout=self._llm_timeout_s,
                )
            except asyncio.TimeoutError:
                return (
                    f"[chat timed out after {self._llm_timeout_s:.0f}s "
                    f"on turn {turn + 1}/{self.MAX_CHAT_TURNS}]"
                )

            last_response = response
            self._memory.append(LLMMessage(role="assistant", content=response.content or "", tool_calls=response.tool_calls))

            if not response.tool_calls:
                break

            # Execute tools silently in chat mode
            self.status = AgentStatus.ACTING
            for tc in response.tool_calls:
                self.current_tool = tc.name
                tool_result = await self._dispatch_tool(tc)
                self.current_tool = None
                self._memory.append(LLMMessage(
                    role="tool",
                    content=tool_result.output if tool_result.success else f"Error: {tool_result.error}",
                    tool_call_id=tc.id,
                    name=tc.name,
                ))
            self.status = AgentStatus.THINKING

        self.status = AgentStatus.IDLE
        self.current_turn = 0
        self.total_turns = 0

        if last_response is None:
            return ""

        # Publish to message bus
        await self.message_bus.publish(Message(
            sender=self.agent_id,
            topic="agent.chat",
            content=last_response.content,
            msg_type="text",
        ))

        return last_response.content

    def _build_task_prompt(self, task: AgentTask) -> str:
        parts = [
            f"## Task: {task.title}",
            "",
            task.description,
        ]
        if task.context:
            parts.extend(["", "## Context", json.dumps(task.context, indent=2, ensure_ascii=False)])
        if self.tools:
            tool_names = [t.name for t in self.tools]
            parts.extend(["", f"## Available Tools: {', '.join(tool_names)}", "Use tools when needed to complete the task."])
        return "\n".join(parts)

    def set_temperature(self, t: Optional[float]) -> None:
        """Override the per-call temperature for subsequent LLM calls.

        Pass None to revert to the provider default (LLMConfig.temperature).
        Used by the Coder loop to cool down the Coder across rounds —
        high creativity at the start (exploration), low near the end
        (conservative polish).
        """
        self.temperature = t

    def add_memory(self, message: str, role: str = "user"):
        self._memory.append(LLMMessage(role=role, content=message))
        self._truncate_memory()

    def clear_memory(self):
        self._memory.clear()
