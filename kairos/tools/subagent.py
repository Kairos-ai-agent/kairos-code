"""SubagentTool — let the Coder spawn a child agent for a sub-task.

This is the foundation of the agentic CLI's "subagent fork" capability.
We don't have a separate Subagent class — a child Coder is just another
KairosAgent instance pointed at the same project. The tool returns the
child's final text, which the parent Coder sees as a tool result and
can act on.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from kairos.tools.base import BaseTool, ToolResult
# KairosAgent / AgentTask are imported lazily inside execute() to avoid a
# circular import: kairos.agents.base -> kairos.tools.base -> this file
# -> kairos.agents.base (partially initialised at top-of-module).

class SubagentTool(BaseTool):
    """Spawn a child agent (Coder) to do a focused sub-task.

    The child runs synchronously — the parent Coder waits for the result
    and continues with it in hand. Useful for "explore the repo and tell
    me what you find", "draft a unit test for this function", "investigate
    this error in the logs" — anything that doesn't need the parent's
    state.

    The child gets the parent's project_id but a fresh memory (no
    accumulated conversation) so it doesn't leak the parent's reasoning.
    """

    name = "spawn_subagent"
    description = (
        "Spawn a child Coder agent to do a focused sub-task. The child "
        "has its own conversation memory and shares your tools + "
        "project_id. Returns the child's final text."
    )

    # The tool needs to know which agent is its parent and which project
    # it's working in. We set these from the Orchestrator when the
    # project is created.
    parent_agent: Optional[KairosAgent] = None
    project_id: str = ""

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string",
                             "description": "Clear, focused description of what the subagent should do"},
                    "max_turns": {"type": "integer", "default": 8,
                                   "description": "Cap the subagent's tool turns. Default 8."},
                    "background": {"type": "boolean", "default": False,
                                    "description": "R38.6.4 (long-running-harness-inspired): if true, return a handle "
                                                    "immediately and run the child in background. Use "
                                                    "subagent_status(handle) / subagent_result(handle) "
                                                    "to poll the result. The parent Coder keeps going."},
                },
                "required": ["task"],
            },
        }

    async def execute(self, task: str = "", max_turns: int = 8,
                      background: bool = False, **kwargs) -> ToolResult:
        if not task:
            return ToolResult(success=False, output="", error="task is required")
        if self.parent_agent is None:
            return ToolResult(success=False, output="",
                              error="SubagentTool not wired to a parent agent")

        # Build a child Coder. We reuse the parent's LLM config so the
        # child uses the same model. Fresh memory = no inherited context.
        from kairos.agents.roles import Coder
        child_id = f"{self.project_id}.sub_{uuid.uuid4().hex[:6]}"
        # Lazy import to break the agents.base <-> tools.base cycle.
        from kairos.agents.base import AgentTask
        try:
            child = Coder(
                agent_id=child_id,
                llm_config=self.parent_agent._llm_config,
                message_bus=self.parent_agent.message_bus,
                tools=self.parent_agent.tools,  # share tools
                # Provenance is handed down, never reset: a child able to act on
                # what its parent read would be a way around the gate. (The
                # constructor would inherit it from the run context anyway; being
                # explicit means it still holds wherever the child is built.)
                taint=getattr(self.parent_agent, "taint", None),
                sentinel=getattr(self.parent_agent, "sentinel", None),
            )
            child.MAX_TOOL_TURNS = min(int(max_turns), 25)
        except Exception as e:
            return ToolResult(success=False, output="",
                              error=f"failed to build child: {e}")

        await self.parent_agent.message_bus.publish(Message(
            sender=child_id, topic="subagent.spawned",
            content=f"Spawned child Coder for task: {task[:120]}",
            msg_type="text",
            metadata={"project_id": self.project_id,
                      "parent": self.parent_agent.agent_id,
                      "child": child_id},
        ))

        child_task = AgentTask(
            id=uuid.uuid4().hex[:8],
            title="Sub-task",
            description=task,
            context={"project_id": self.project_id},
        )

        # R38.6.4 (long-running-harness-inspired): background mode returns
        # immediately with a handle, so the parent Coder can keep
        # making progress. The child runs as an asyncio.Task in
        # the background; status / result are pollable via
        # LongRunningRegistry.
        if background:
            from kairos.long_running import get_registry
            def _child_coro():
                return child.run(child_task)
            handle = get_registry().spawn(
                parent_id=self.parent_agent.agent_id,
                project_id=self.project_id,
                task_text=task[:200],
                coro_factory=_child_coro,
            )
            return ToolResult(
                success=True,
                output=(f"Sub-agent spawned in background. "
                        f"handle={handle} child={child_id}. "
                        f"Use subagent_status({handle}) to poll "
                        f"the result."),
                metadata={"child_agent_id": child_id,
                          "handle": handle, "background": True},
            )

        try:
            result = await child.run(child_task)
        except Exception as e:
            return ToolResult(success=False, output="",
                              error=f"subagent crashed: {e}")

        await self.parent_agent.message_bus.publish(Message(
            sender=child_id, topic="subagent.completed",
            content=result[:500],
            msg_type="result",
            metadata={"project_id": self.project_id,
                      "child": child_id},
        ))
        return ToolResult(
            success=True,
            output=result[:5000],
            metadata={"child_agent_id": child_id},
        )

from kairos.core.message_bus import Message  # noqa: E402  (after class for forward ref)