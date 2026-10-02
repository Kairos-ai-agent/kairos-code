"""SubagentTool — let the Coder spawn a child agent for a sub-task.

This is the foundation of the agentic CLI's "subagent fork" capability.
We don't have a separate Subagent class — a child Coder is just another
KairosAgent instance pointed at the same project.

The parent does **not** receive the child's raw transcript. A child can
burn tens of thousands of tokens exploring, and handing that output
straight back spends the parent's remaining context on the child's
working notes rather than on what the child concluded. So the child's
report is distilled to a short summary before it becomes a tool result,
and the full text is written to disk so the parent can read any part of
it on demand instead of carrying all of it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from kairos.llm.base import LLMMessage
from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# KairosAgent / AgentTask are imported lazily inside execute() to avoid a
# circular import: kairos.agents.base -> kairos.tools.base -> this file
# -> kairos.agents.base (partially initialised at top-of-module).

#: Results shorter than this are passed through untouched — summarising them
#: costs a round-trip and saves nothing.
_DISTILL_MIN_CHARS = 2000

#: If distillation fails we still have to return *something* bounded.
_DISTILL_FALLBACK_CHARS = 5000

#: Head/tail budget for the text handed to the summariser. A child report can
#: itself be longer than the model's window; conclusions live at both ends
#: (the answer up front, the caveats at the back).
_DISTILL_INPUT_CHARS = 12000

_DISTILL_PROMPT = (
    "Condense this sub-agent's report for the engineer who delegated the "
    "task. Keep, in this order:\n"
    "  1. The answer to the delegated task — the conclusion, first.\n"
    "  2. Concrete, actionable findings: file paths, line ranges, symbol "
    "names, commands, exact values.\n"
    "  3. Decisions taken and anything the sub-agent could not resolve.\n"
    "Drop narrative, restated instructions, tool-by-tool play-by-play, and "
    "anything that only makes sense with the raw output in front of you.\n"
    "Be terse. Bullet points. No preamble, no sign-off. Under 400 words.\n\n"
    "DELEGATED TASK: {task}\n\n"
    "SUB-AGENT REPORT:\n{body}"
)

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
        "project_id. You receive a CONDENSED SUMMARY of the child's work, "
        "plus the path where its full report was saved — read that file if "
        "you need a detail the summary left out."
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

    def _persist_raw_output(self, child_id: str, task: str,
                            text: str) -> str | None:
        """Write the child's full report where the parent can read it later.

        This is what keeps distillation lossless: the summary is what the
        parent *carries*, the file is what it can *go and get*. Best-effort —
        delegation must not fail because a cache directory was read-only.

        The text is passed through ``sentinel.redact`` first: a child that has
        read config files can quote an API key back at us, and this file is a
        new place where that could end up on disk. Old reports are pruned on
        the same schedule as the sentinel's own logs.
        """
        try:
            from kairos.config.settings import settings as ksettings
            try:
                from kairos.sentinel import redact
                safe_text = redact(text)
            except Exception:  # noqa: BLE001 — redaction must never block
                logger.debug("sentinel.redact unavailable for sub-agent output",
                             exc_info=True)
                safe_text = text
            out_dir = Path(ksettings.data_dir) / "subagent_outputs"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{child_id}.md"
            path.write_text(
                f"# Sub-agent report\n\n- child: {child_id}\n"
                f"- task: {task}\n\n---\n\n{safe_text}\n",
                encoding="utf-8",
            )
            self._prune_old_outputs(out_dir)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not persist sub-agent output: %s", exc)
            return None

    @staticmethod
    def _prune_old_outputs(out_dir: Path,
                           keep_days: int = 14, keep_files: int = 200) -> None:
        """Bound the growth of the report directory. Never raises."""
        try:
            reports = sorted(
                out_dir.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            cutoff = time.time() - keep_days * 86400
            for stale in reports[keep_files:]:
                try:
                    stale.unlink()
                except OSError:
                    pass
            for old in reports[:keep_files]:
                try:
                    if old.stat().st_mtime < cutoff:
                        old.unlink()
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            logger.debug("sub-agent output pruning skipped", exc_info=True)

    async def _distill_result(self, task: str, result: str) -> tuple:
        """Return ``(text_for_the_parent, summarized?)``. Never raises.

        Short reports are passed through — a summarisation round-trip that
        saves nothing is pure latency. Long ones are condensed by the parent's
        own model, and if that call fails we fall back to the previous
        behaviour (a hard character cut), so this can only ever be an
        improvement on what the parent used to receive.
        """
        if len(result) <= _DISTILL_MIN_CHARS:
            return result, False

        body = result
        if len(body) > _DISTILL_INPUT_CHARS * 2:
            omitted = len(body) - 2 * _DISTILL_INPUT_CHARS
            body = (
                body[:_DISTILL_INPUT_CHARS]
                + f"\n\n… [{omitted} chars omitted] …\n\n"
                + body[-_DISTILL_INPUT_CHARS:]
            )
        prompt = _DISTILL_PROMPT.format(task=task[:500], body=body)
        try:
            parent = self.parent_agent
            timeout = getattr(parent, "_llm_timeout_s", 120) or 120
            response = await asyncio.wait_for(
                parent._llm.complete([LLMMessage(role="user", content=prompt)]),
                timeout=timeout,
            )
            summary = (response.content or "").strip()
            if summary:
                logger.debug(
                    "distilled sub-agent report: %d → %d chars",
                    len(result), len(summary),
                )
                return summary, True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sub-agent distillation failed (%s); falling back to a "
                "character cut", exc,
            )
        return result[:_DISTILL_FALLBACK_CHARS], False

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

        # The parent carries the summary; the full report stays on disk. A
        # child that explored for 40 turns should not hand its whole
        # transcript to a parent that is already deep into its own context.
        text, summarized = await self._distill_result(task, result)
        raw_path = self._persist_raw_output(child_id, task, result)
        output = text
        if raw_path:
            output = (
                f"{text}\n\n[full report saved: {raw_path} "
                f"({len(result)} chars) — read it if a detail is missing]"
            )
        return ToolResult(
            success=True,
            output=output,
            metadata={"child_agent_id": child_id,
                      "summarized": summarized,
                      "result_chars": len(result),
                      "raw_output_path": raw_path},
        )

from kairos.core.message_bus import Message  # noqa: E402  (after class for forward ref)