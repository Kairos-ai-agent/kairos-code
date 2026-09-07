"""KairosAgent - Base class for all agents in the system."""

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
    # the cloud task-Harness-style output guardrail verdict. Populated by the
    # agent's run() when an output_guardrail is attached. Optional
    # so existing call sites that build AgentTask without it keep
    # working unchanged.
    guardrail: Optional[Dict[str, Any]] = None

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
    # R38.6.4 #1: project_id is set by the orchestrator so _chat_impl
    # can pull project context (AGENTS.md, file tree, history) into
    # the system prompt. Optional — chat() still works without it.
    project_id: Optional[str] = None

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
            with self._traced_llm_call(messages, tools) as _trace_span:
                stream_ctx = self._llm.stream(messages, tools=tools, temperature=self.temperature)
        except Exception as e:
            logger.debug("stream() unavailable, falling back to complete(): %s", e)
            with self._traced_llm_call(messages, tools) as _trace_span:
                resp = await self._llm.complete(messages, tools=tools, temperature=self.temperature)
            _trace_span.set_output(
                content=resp.content,
                prompt_tokens=resp.usage.get("prompt_tokens", 0),
                completion_tokens=resp.usage.get("completion_tokens", 0),
                finish_reason=resp.finish_reason,
            )
            return resp

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
                with self._traced_llm_call(messages, tools) as _trace_span:
                    resp = await self._llm.complete(messages, tools=tools, temperature=self.temperature)
                _trace_span.set_output(
                    content=resp.content,
                    prompt_tokens=resp.usage.get("prompt_tokens", 0),
                    completion_tokens=resp.usage.get("completion_tokens", 0),
                    finish_reason=resp.finish_reason,
                )
                return resp
            except Exception:
                # Re-raise the original stream error if complete also fails.
                raise

        # Record the streaming call's output on the trace span
        # (started at the top of _stream_complete). usage is populated
        # by the underlying provider if it tracks it; otherwise empty.
        try:
            _trace_span.set_output(
                content=accumulated_text[:500] if accumulated_text else "",
                prompt_tokens=int(usage.get("prompt_tokens", 0)) if usage else 0,
                completion_tokens=int(usage.get("completion_tokens", 0)) if usage else 0,
                finish_reason=finish_reason,
            )
        except Exception:
            pass
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
        project_dir: Optional[str] = None,
        output_guardrail: Optional[Any] = None,
        plan_tracker: Optional[Any] = None,
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

        # Round 11: structured plan tracker (TodoWrite-style). If set,
        # the agent intercepts the ``write_todos`` tool call and
        # applies the diff to the plan instead of dispatching it as a
        # real tool. See kairos.loop.plan.Plan.
        self.plan_tracker = plan_tracker

        # State
        self.status = AgentStatus.IDLE
        self.current_task: Optional[AgentTask] = None
        # Live progress fields (read by .state below and updated each turn
        # so the WS heartbeat can push them out to the UI).
        self.current_turn: int = 0
        self.total_turns = 0
        self.current_tool: Optional[str] = None

        # Concurrency protection
        self._lock = asyncio.Lock()

        # Memory with token counting
        self._memory: List[LLMMessage] = []
        self._max_tokens = 80000  # Token budget
        self._keep_recent = 4     # Always keep last N messages

        # the cloud task-Harness-style retained reasoning: a running summary of
        # older conversation turns is kept alongside the raw recent
        # messages. The summary is regenerated every SUMMARIZE_EVERY_N
        # turns (or when memory would otherwise overflow). It is
        # injected into the system_prompt as a single "earlier context"
        # block so the model keeps long-term memory without us having
        # to fit every historical message into the context window.
        self._memory_summary: str = ""
        self._summarize_every_n: int = 8
        self._last_summarized_at_turn: int = 0

        # Message bus subscription
        self.message_bus.subscribe(agent_id, f"agent.{agent_id}")
        self.message_bus.subscribe(agent_id, "broadcast")

        # the cloud task-Harness-style project context: AGENTS.md augments the
        # hard-coded system_prompt at construction time. Skills are
        # loaded per task at _build_messages() because matching depends
        # on the current task context (keyword / tools / filename).
        # Both loaders are optional — if project_dir is None, the agent
        # behaves exactly as before.
        self._project_dir = Path(project_dir) if project_dir else None
        if self._project_dir is not None:
            try:
                from kairos.agents_md import AgentsMdLoader
                from kairos.skills import SkillsLoader
                self._agents_md_loader = AgentsMdLoader(
                    project_dir=self._project_dir,
                )
                self._skills_loader = SkillsLoader(
                    project_dir=self._project_dir,
                )
                self.system_prompt = (
                    self._agents_md_loader.merge_into_system_prompt(
                        self.system_prompt
                    )
                )
            except Exception as exc:
                # Don't fail agent construction if AGENTS.md or skills
                # can't be read — log and continue with the hard-coded
                # prompt.
                logger.warning(
                    "Failed to load AGENTS.md / skills for %s: %s",
                    self._project_dir, exc,
                )
                self._agents_md_loader = None
                self._skills_loader = None
        else:
            self._agents_md_loader = None
            self._skills_loader = None

        # R38.6 §34: self-improving style FTS5 memory — pull top-5
        # project-scoped memories into the system prompt so
        # the agent remembers past sessions without
        # re-asking the user. Lazy-loaded on first use.
        self._memory_kb = None
        if self._project_dir:
            try:
                from kairos.memory_kb import MemoryKB
                mem_path = self._project_dir / ".kairos" / "memory_kb.json"
                if mem_path.parent.exists():
                    kb = MemoryKB(storage_path=mem_path)
                    # Only do a quick recall at construction; full
                    # recall runs on each task. Avoid hitting the
                    # network on every agent spawn.
                    self._memory_kb = kb
            except Exception as exc:
                logger.debug("memory_kb init failed: %s", exc)
                self._memory_kb = None

        # Optional output guardrail (the cloud task-Harness-style hook). When
        # present, ``run()`` invokes it on the final result before
        # returning so a Reviewer agent (or a fast local check) can
        # flag the output. Stays None by default — enabling it costs
        # an extra LLM round-trip and not every project needs it.
        self._output_guardrail = output_guardrail

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

    # ------------------------------------------------------------------
    # Observability (Round 11)
    # ------------------------------------------------------------------
    def _traced_llm_call(self, messages, tools=None):
        """Return a context manager that wraps an LLM call in a tracer
        span. The default tracer is a no-op (in-memory) so this is
        safe to call from any code path; production users wire
        Langfuse / OTLP via ``init_default_tracer()``.

        Usage:
            with self._traced_llm_call(messages, tools) as span:
                response = await self._llm.complete(messages, tools=tools)
                span.set_output(...)
        """
        from kairos.observability import get_default_tracer
        tracer = get_default_tracer()
        # Build a list-shaped copy for the message_count attribute
        msgs_list = list(messages) if messages else []
        return tracer.llm_call(
            model=getattr(self._llm_config, "model", "unknown"),
            provider=getattr(self._llm_config, "provider", ""),
            messages=msgs_list,
        )

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

    @staticmethod
    def _sanitize_memory(mem: List[LLMMessage]) -> List[LLMMessage]:
        """Drop ``tool`` messages that aren't a valid reply to a preceding
        assistant ``tool_calls`` message.

        OpenAI rejects a ``role='tool'`` message unless the immediately
        preceding message is an assistant message with ``tool_calls``
        (and the tool message's ``tool_call_id`` matches one of them).
        An interrupted loop / tool call can leave a dangling ``tool``
        message at the end of ``self._memory``; sending it raises a 400
        ("Messages with role 'tool' must be a response to a preceding
        message with 'tool_calls'"). We re-validate at build time so a
        stale orphan is simply dropped instead of breaking the call.
        """
        out: List[LLMMessage] = []
        for m in mem:
            if m.role == "tool":
                prev = out[-1] if out else None
                if prev is None or not getattr(prev, "tool_calls", None):
                    continue  # no preceding tool_calls → orphaned → drop
                if m.tool_call_id:
                    ids = {getattr(tc, "id", "") for tc in prev.tool_calls}
                    if ids and m.tool_call_id not in ids:
                        continue
                out.append(m)
            else:
                out.append(m)
        return out

    def _build_messages(self) -> List[LLMMessage]:
        """Build messages for LLM including system prompt and memory."""
        self._truncate_memory()
        system = self.system_prompt
        # R38.6 §34: self-improving style FTS5 memory — pull top-5
        # project-scoped memories relevant to the current task
        # and append them to the system prompt. This is the
        # canonical "agent that grows with you" feature.
        if self._memory_kb is not None and self.current_task is not None:
            try:
                task_text = (self.current_task.title or "") + " " + \
                            (self.current_task.description or "")
                hits = self._memory_kb.recall(task_text.strip(),
                                                scope="project", limit=5)
                if hits:
                    lines = ["", "## Memory (from past sessions)"]
                    for h in hits:
                        val = h.value
                        if not val:
                            continue
                        if not isinstance(val, str):
                            val = str(val)
                        lines.append(f"- {h.key}: {val[:200]}")
                    if len(lines) > 1:
                        system = system + "\n" + "\n".join(lines)
            except Exception as exc:
                logger.debug("memory recall in _build_messages: %s", exc)
        # Inject the cloud task-style skills based on current task + tool context.
        # We do this every turn because the active tool list changes
        # after each tool call, which can promote/demote skills.
        if self._skills_loader is not None and self.current_task is not None:
            try:
                task = self.current_task
                ctx = {
                    "title": task.title,
                    "description": task.description,
                    "tools": [
                        tc.name for tc in (
                            (self._memory[-1].tool_calls or [])
                            if self._memory and self._memory[-1].tool_calls
                            else []
                        )
                    ] or [
                        # If we haven't called any tool yet, expose
                        # the agent's known tool names so keyword /
                        # tool filters can match the agent's domain.
                        t.name for t in self.tools
                    ],
                }
                skills_block = self._skills_loader.for_context(ctx)
                if skills_block:
                    system = f"{system}\n\n{skills_block}"
            except Exception as exc:
                logger.debug("skills injection failed: %s", exc)

        # Round 12: inject the TodoWrite-style plan into the system
        # prompt so the Coder can see what it committed to last turn.
        # The plan is short (max ~6KB at DEFAULT_MAX_ACTIVE=3 todos) so
        # this is cheap; the agent can use it to avoid re-doing done
        # work and to know what's in_progress right now.
        if self.plan_tracker is not None and not self.plan_tracker.is_empty:
            try:
                from kairos.loop.plan import render_plan_block
                plan_block = render_plan_block(self.plan_tracker)
                if plan_block:
                    system = f"{system}\n\n{plan_block}"
            except Exception as exc:
                logger.debug("plan injection failed: %s", exc)

        # Inject retained-reasoning summary (the cloud task-Harness-style) as a
        # second system message, immediately after the role + skills
        # system prompt. This is the agent's "earlier context" memory
        # for the long-running session.
        msgs: List[LLMMessage] = [LLMMessage(role="system", content=system)]
        if self._memory_summary:            msgs.append(LLMMessage(
                role="system",
                content=(
                    "# Earlier conversation summary\n"
                    "The following is a compact summary of turns that have\n"
                    "been compacted out of the active context window. Treat\n"
                    "it as authoritative for anything not contradicted by\n"
                    "the recent messages below.\n\n"
                    f"{self._memory_summary}"
                ),
            ))
        msgs.extend(self._sanitize_memory(self._memory))
        return msgs

    async def _maybe_summarize_memory(self, current_turn: int) -> None:
        """Periodically condense the older memory into a running summary.

        Triggered every `_summarize_every_n` turns OR when memory has
        grown past 80% of the token budget. The summary is *added to*
        (not replaced) so we don't lose information between snapshots:
        new turns contribute a delta on top of the prior summary.

        We always keep the most recent `_keep_recent` messages verbatim
        so the model can reference the latest tool calls without having
        to query the summary.
        """
        # Don't bother until there's something to summarize.
        if len(self._memory) <= self._keep_recent:
            return
        threshold_turn = (
            self._last_summarized_at_turn + self._summarize_every_n
        )
        token_total = sum(
            self._count_tokens(m.content) for m in self._memory
        )
        over_budget = token_total > int(self._max_tokens * 0.8)
        if current_turn < threshold_turn and not over_budget:
            return

        # Pick the older half to compact. The most recent slice is
        # preserved verbatim regardless.
        keep = self._keep_recent
        older = self._memory[:-keep] if len(self._memory) > keep else []
        if not older:
            return
        try:
            transcript = "\n\n".join(
                f"[{m.role}] {m.content[:1500]}" for m in older
            )
            prior = self._memory_summary
            prompt = (
                "You are compressing a long agent transcript into a "
                "running summary. Preserve:\n"
                "  1. Decisions made and the rationale\n"
                "  2. Tools called and the paths/files they touched\n"
                "  3. Errors hit and how they were resolved\n"
                "  4. Open questions and remaining work\n"
                "Be terse. Use bullet points. Target 200-400 words.\n\n"
            )
            if prior:
                prompt += (
                    f"# Existing summary (merge into, do not repeat):\n"
                    f"{prior}\n\n# New turns to fold in:\n{transcript}"
                )
            else:
                prompt += f"# Turns to summarize:\n{transcript}"
            with self._traced_llm_call([LLMMessage(role="user", content=prompt)]) as _trace_span:
                # Wrap with the same per-call timeout as the main loop so a
                # hung summarize can't stall the whole run() forever.
                response = await asyncio.wait_for(
                    self._llm.complete([LLMMessage(role="user", content=prompt)]),
                    timeout=self._llm_timeout_s,
                )
                _trace_span.set_output(
                    content=(response.content or "")[:500],
                    prompt_tokens=response.usage.get("prompt_tokens", 0),
                    completion_tokens=response.usage.get("completion_tokens", 0),
                    finish_reason=response.finish_reason,
                )
            new_summary = (response.content or "").strip()
            if new_summary:
                self._memory_summary = new_summary
                self._last_summarized_at_turn = current_turn
                logger.debug(
                    "%s: memory summarized at turn %d (%d chars)",
                    self.agent_id, current_turn, len(new_summary),
                )
        except Exception as exc:
            # Summarization is best-effort. A failure here shouldn't
            # break the agent loop.
            logger.warning(
                "%s: memory summarization failed: %s",
                self.agent_id, exc,
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
            # Sentinel: stays True only if we ran out of turns without
            # breaking out of the for loop (i.e. every turn had at least
            # one tool call, so the agent never converged to a final
            # answer). See the post-loop `if hit_turn_limit` below.
            hit_turn_limit = True
            for turn in range(self.MAX_TOOL_TURNS):
                self.status = AgentStatus.THINKING
                self.current_turn = turn + 1
                self.current_tool = None
                # Announce this turn on the bus. R38.6.3: send to
                # the dedicated ``agent.progress`` topic so the chat
                # thread can filter it out and the Workbench can
                # subscribe separately. The chat view should
                # only show the agent's actual responses, not
                # per-turn reasoning chatter.
                await self.message_bus.publish(Message(
                    sender=self.agent_id,
                    topic="agent.progress",
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
                    # This is a timeout, NOT a tool-call-limit: clear the
                    # sentinel so the post-loop block below doesn't overwrite
                    # our timeout message with "Tool call limit reached".
                    hit_turn_limit = False
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
                        content=interim[:2000],
                        msg_type="text",
                        metadata={"task_id": task.id, "turn": turn + 1},
                    ))

                # No tool calls -> done
                if not response.tool_calls:
                    result = response.content
                    hit_turn_limit = False
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

                    # Round 11: built-in ``write_todos`` interception.
                    # The LLM emits TodoWrite-style plan updates as a
                    # tool call; we apply the diff to the plan_tracker
                    # and short-circuit the dispatch so the tool
                    # itself is never actually invoked.
                    if tc.name == "write_todos" and self.plan_tracker is not None:
                        tool_result = await self._handle_write_todos(
                            tc.arguments if isinstance(tc.arguments, dict) else {},
                            task=task, turn=turn,
                        )
                        self.current_tool = None
                        # Memory injection (matches real tool result shape)
                        self._memory.append(LLMMessage(
                            role="tool",
                            content=tool_result.output if tool_result.success else f"Error: {tool_result.error}",
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))
                        # Publish result on the bus so the chat history
                        # shows the agent's plan update.
                        await self.message_bus.publish(Message(
                            sender=self.agent_id,
                            topic="tool.result",
                            content=tool_result.output[:500] if tool_result.success else f"Error: {tool_result.error}",
                            msg_type="text",
                            metadata={"task_id": task.id, "tool": tc.name,
                                      "turn": turn + 1,
                                      "success": tool_result.success},
                        ))
                        continue

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

                # End-of-turn: condense older turns into a running
                # summary (the cloud task-Harness-style retained reasoning).
                # Runs only every _summarize_every_n turns or when
                # memory approaches the token budget, so per-turn cost
                # is usually zero. Indented 16 spaces so it lives
                # inside the for-turn loop body.
                await self._maybe_summarize_memory(turn + 1)

            # We can't use a bare `for/else` here because we're inside
            # a try block (Python parses `else` as a try-else clause).
            # The `hit_turn_limit` sentinel below is set to False by
            # the inner `if not response.tool_calls: break` branch; if
            # it's still True after the loop, every turn produced at
            # least one tool call and the agent never converged.
            if hit_turn_limit:
                result = f"Tool call limit reached after {self.MAX_TOOL_TURNS} turns."

            # Publish final result
            await self.message_bus.publish(Message(
                sender=self.agent_id,
                topic="task.result",
                content=result[:2000],
                msg_type="result",
                metadata={"task_id": task.id},
            ))

            task.status = "completed" if not timed_out else "failed"
            task.result = result

            # the cloud task-Harness-style output guardrail hook. If a reviewer
            # guardrail is attached, let it grade the final result.
            # The guardrail publishes its own verdict to the bus; we
            # also stamp the task with a "guardrail" flag so the
            # orchestrator can decide whether to re-dispatch the
            # Coder or surface a warning. We do NOT mutate ``result``
            # itself — the Coder's answer is preserved verbatim so
            # the user can read it even when the guardrail trips.
            if self._output_guardrail is not None:
                try:
                    g_result = await self._output_guardrail.check(
                        self.agent_id, result,
                        context={"task_id": task.id, "role": self.role},
                    )
                    task.guardrail = g_result.to_dict()
                    if g_result.tripwire:
                        logger.warning(
                            "%s: output guardrail tripped (%s): %s",
                            self.agent_id, g_result.severity,
                            g_result.summary,
                        )
                except Exception as exc:
                    logger.warning(
                        "%s: guardrail raised %s; treating as pass",
                        self.agent_id, exc,
                    )
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

    def _build_chat_system_prompt(self) -> str:
        """Build a project-aware system prompt for single-turn chat.

        R38.6.4 #1+#2+#6: without this, ``chat()`` was a stateless
        "You are a helpful assistant" call. With this, the Coder
        knows which project it's in, the AGENTS.md rules, the
        most recent loop conclusions, the project-level preferences
        and known fixes — so a casual "what does this function
        do?" or "fix this bug" gets a real, project-grounded answer.

        Every field is best-effort: if the work_dir doesn't exist,
        the orchestrator is gone, or persistence returns an empty
        list, we degrade silently rather than raise. The chat call
        must never break because context is missing.
        """
        if not self.project_id:
            # No project context: fall back to the generic prompt.
            return ("You are a helpful assistant. Respond "
                    "conversationally to the user's message. Use "
                    "tools when helpful.")
        try:
            orch = self._orchestrator  # injected by orchestrator
        except AttributeError:
            orch = None
        project = None
        if orch is not None:
            try:
                project = orch.get_project(self.project_id)
            except Exception:
                project = None
        wd = ""
        if project is not None:
            wd = (getattr(project, "work_dir", None)
                  or str(getattr(project, "workspace", "")))

        blocks: list[str] = []

        # 1) project identity
        if project is not None:
            name = getattr(project, "name", "") or project.id
            desc = (getattr(project, "description", "") or "").strip()
            head = f'You are the Coder for project {name}.'
            if desc:
                head += f"  {desc[:200]}"
            blocks.append(head)

        # 2) AGENTS.md content (cap 2k chars)
        try:
            from pathlib import Path as _P
            agents_md = _P(wd) / "AGENTS.md"
            if agents_md.is_file():
                txt = agents_md.read_text(
                    encoding="utf-8", errors="replace")
                blocks.append("### AGENTS.md (excerpt)\n"
                              + txt[:2000])
        except Exception:
            pass

        # 3) Recent loop conclusions + known issues (auto-memory)
        if orch is not None and hasattr(orch, "_db"):
            db = orch._db
            try:
                rounds = db.load_loop_rounds(
                    self.project_id, limit=3) or []
            except Exception:
                rounds = []
            if rounds:
                lines = ["### Recent loop conclusions"]
                for r in rounds:
                    cs = (r.get("coder_summary") or "").strip()
                    if cs:
                        lines.append(
                            f"- R{r.get('round','?')}: {cs[:200]}")
                if len(lines) > 1:
                    blocks.append("\n".join(lines))
            try:
                fixes = db._get_log_path()  # type: ignore[attr-defined]
            except Exception:
                fixes = None
            # working_fixes rows: pull via a small helper if present
            try:
                wf_rows = db.conn.execute(  # type: ignore[attr-defined]
                    "SELECT issue, fix, severity, created_at "
                    "FROM working_fixes WHERE project_id = ? "
                    "ORDER BY created_at DESC LIMIT 5",
                    (self.project_id,)).fetchall()
            except Exception:
                wf_rows = []
            if wf_rows:
                lines = ["### Known issues to avoid"]
                for issue, fix, sev, _ts in wf_rows:
                    lines.append(
                        f"- [{sev}] {issue[:80]} — fix: {fix[:120]}")
                blocks.append(" ".join(lines))

        # 4) assemble
        base = ("You are a helpful assistant. Respond "
                "conversationally to the user's message. Use "
                "tools when helpful.")
        if blocks:
            ctx = " ".join(blocks)
            return (f"{base}\n\n"
                    f"You have the following project context:\n"
                    f"{ctx}")
        return base

    async def _chat_impl(self, message: str) -> str:
        """Internal chat implementation (called with lock held)."""
        user_msg = LLMMessage(role="user", content=message)
        self._memory.append(user_msg)

        tool_schemas = self._get_tool_schemas()

        # Use conversational prompt for chat mode (not task-specific JSON prompt)
        chat_system = self._build_chat_system_prompt()

        self.current_turn = 0
        self.total_turns = self.MAX_CHAT_TURNS
        last_response = None
        for turn in range(self.MAX_CHAT_TURNS):
            self.current_turn = turn + 1
            self._truncate_memory()
            messages = [LLMMessage(role="system", content=chat_system)] + self._sanitize_memory(self._memory)
            try:
                with self._traced_llm_call(messages, tool_schemas) as _trace_span:
                    response = await asyncio.wait_for(
                        self._llm.complete(messages, tools=tool_schemas, temperature=self.temperature),
                        timeout=self._llm_timeout_s,
                    )
                _trace_span.set_output(
                    content=(response.content or "")[:500],
                    prompt_tokens=response.usage.get("prompt_tokens", 0),
                    completion_tokens=response.usage.get("completion_tokens", 0),
                    finish_reason=response.finish_reason,
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

        # Publish to message bus — but only when there's actually
        # something to say. An empty / whitespace-only reply (e.g. a
        # provider that returned blank content on a transient error)
        # used to publish an empty `agent.chat` event, which the chat
        # thread rendered as an empty "Kairos" bubble.
        reply_text = (last_response.content or "").strip()
        if reply_text:
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
