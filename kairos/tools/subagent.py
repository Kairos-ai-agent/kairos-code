"""SubagentTool — let the Coder spawn a child agent for a sub-task.

This is the foundation of Claude Code's "subagent fork" capability.
We don't have a separate Subagent class — a child Coder is just another
KairosAgent instance pointed at the same project. The tool returns the
child's final text, which the parent Coder sees as a tool result and
can act on.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from kairos.agents.base import AgentTask, KairosAgent
from kairos.tools.base import BaseTool, ToolResult


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
                },
                "required": ["task"],
            },
        }

    async def execute(self, task: str = "", max_turns: int = 8,
                      **kwargs) -> ToolResult:
        if not task:
            return ToolResult(success=False, output="", error="task is required")
        if self.parent_agent is None:
            return ToolResult(success=False, output="",
                              error="SubagentTool not wired to a parent agent")

        # Build a child Coder. We reuse the parent's LLM config so the
        # child uses the same model. Fresh memory = no inherited context.
        from kairos.agents.roles import Coder
        child_id = f"{self.project_id}.sub_{uuid.uuid4().hex[:6]}"
        try:
            child = Coder(
                agent_id=child_id,
                llm_config=self.parent_agent._llm_config,
                message_bus=self.parent_agent.message_bus,
                tools=self.parent_agent.tools,  # share tools
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