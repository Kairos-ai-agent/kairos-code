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
from typing import Any, Dict, Optional

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
                                                   "immediately and run the child in background. Use the "
                                                   "subagent_status and subagent_result tools "
                                                   "(with the handle) to poll and collect the result. "
                                                   "The parent Coder keeps going."},
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
            # ``None`` means the parent runs with no per-call limit (a local
            # model on a small box): a bare ``or 120`` turned that into a
            # 2-minute cap. Only a missing/non-numeric/zero attribute falls back
            # to the 120s default.
            timeout = getattr(parent, "_llm_timeout_s", 120)
            if timeout is not None and (not isinstance(timeout, (int, float))
                                        or timeout <= 0):
                timeout = 120
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
                        f"Poll it with the subagent_status tool, then read "
                        f"the report with subagent_result."),
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


#: Largest result body handed back inline by ``subagent_result``. A child's
#: full report can be tens of thousands of characters and the parent asked for
#: a result, not a transcript; larger bodies are still written to disk
#: (redacted) so the parent can go and read the rest.
_RESULT_INLINE_CHARS = 8000


class _SubagentHandleTool(BaseTool):
    """Shared plumbing for the read-only ``subagent_status`` / ``subagent_result``.

    Both read the process-local :class:`~kairos.long_running.LongRunningRegistry`
    that ``SubagentTool(background=True)`` spawns into. They are handed the
    *spawn* tool instance and read its ``parent_agent`` / ``project_id`` at call
    time — the orchestrator wires those onto the spawn tool
    (``kairos/core/orchestrator_parts/wiring.py``), so "which session owns these
    handles" is decided in exactly one place.

    Scoping: a handle resolves only when the record's ``parent_id`` *and*
    ``project_id`` both match this session's. Anything else returns the same
    "no such handle" answer an unknown handle gets, so these tools can never be
    used to enumerate or read another session's sub-agents. Bodies are passed
    through ``sentinel.redact`` and bounded before they reach the parent's
    context.
    """

    #: The SubagentTool whose parent_agent / project_id scope these lookups.
    spawn_tool: Optional[SubagentTool] = None

    def __init__(self, spawn_tool: Optional[SubagentTool] = None,
                 allowed_root: str | Path = "."):
        super().__init__(allowed_root=allowed_root)
        self.spawn_tool = spawn_tool

    def _scoped_record(self, handle: str) -> Optional[Dict[str, Any]]:
        """The registry record for *handle*, or ``None`` if it is not ours.

        Fails closed: no wired session, unknown handle, or a handle owned by
        another parent/project all return ``None`` (the caller reports the same
        "no such sub-agent" for each, so ownership is never disclosed).
        """
        handle = str(handle or "").strip()
        if not handle:
            return None
        spawn = self.spawn_tool
        parent_id = getattr(getattr(spawn, "parent_agent", None), "agent_id", None)
        project_id = getattr(spawn, "project_id", "") or ""
        if not parent_id:
            return None
        try:
            from kairos.long_running import get_registry
            rec = get_registry().status_of(handle)
        except Exception as exc:  # noqa: BLE001 — a registry hiccup is not a crash
            logger.debug("subagent registry lookup failed: %s", exc)
            return None
        if not rec:
            return None
        if rec.get("parent_id") != parent_id or rec.get("project_id") != project_id:
            return None
        return rec

    @staticmethod
    def _redact(text: Any) -> str:
        """Strip credential-shaped substrings. A child that read config files
        can quote a key back at us; this is the point where that stops."""
        text = "" if text is None else str(text)
        if not text:
            return ""
        try:
            from kairos.sentinel import redact
            return redact(text)
        except Exception:  # noqa: BLE001 — redaction must never block a read
            return text


class SubagentStatusTool(_SubagentHandleTool):
    """Read-only status of a background sub-agent spawned by ``spawn_subagent``."""

    name = "subagent_status"
    description = (
        "Check a background sub-agent you started with "
        "spawn_subagent(background=true). Returns its status (pending / "
        "running / completed / failed / cancelled), whether it has finished, "
        "and any error. Read-only — it changes nothing. Once the status is "
        "completed, call subagent_result(handle) to read its report."
    )

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "The handle returned by spawn_subagent.",
                    },
                },
                "required": ["handle"],
                "additionalProperties": False,
            },
        }

    async def execute(self, handle: str = "", **kwargs: Any) -> ToolResult:
        if not str(handle or "").strip():
            return ToolResult(success=False, output="", error="handle is required")
        rec = self._scoped_record(handle)
        if rec is None:
            return ToolResult(success=False, output="",
                              error=f"No such background sub-agent: {handle}")
        finished = rec.get("finished_at") is not None
        lines = [
            f"handle: {rec['handle']}",
            f"status: {rec['status']}",
            f"finished: {'yes' if finished else 'no'}",
        ]
        if rec.get("error"):
            lines.append(f"error: {self._redact(rec['error'])}")
        result_ready = rec.get("result") is not None and not rec.get("error")
        if result_ready:
            lines.append("result ready: call subagent_result(handle) to read it")
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={"handle": rec["handle"], "status": rec["status"],
                      "finished": finished, "result_ready": result_ready},
        )


class SubagentResultTool(_SubagentHandleTool):
    """Read-only report of a finished background sub-agent."""

    name = "subagent_result"
    description = (
        "Read the report of a background sub-agent you started with "
        "spawn_subagent(background=true). Fails while the sub-agent is still "
        "pending or running — call subagent_status(handle) first. The body is "
        "redacted and capped; when it was capped the full report is also saved "
        "to a file and that path is included in the answer."
    )

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "The handle returned by spawn_subagent.",
                    },
                },
                "required": ["handle"],
                "additionalProperties": False,
            },
        }

    async def execute(self, handle: str = "", **kwargs: Any) -> ToolResult:
        if not str(handle or "").strip():
            return ToolResult(success=False, output="", error="handle is required")
        rec = self._scoped_record(handle)
        if rec is None:
            return ToolResult(success=False, output="",
                              error=f"No such background sub-agent: {handle}")
        status = rec.get("status")
        if status in ("pending", "running"):
            return ToolResult(
                success=False, output="",
                error=(f"sub-agent {rec['handle']} is still {status}; poll "
                       f"subagent_status({rec['handle']}) and try again when "
                       f"it is completed"),
            )
        if rec.get("error") and rec.get("result") is None:
            return ToolResult(success=False, output="",
                              error=self._redact(f"sub-agent failed: {rec['error']}"))
        body = self._redact(rec.get("result"))
        if not body:
            return ToolResult(success=True,
                              output="(the sub-agent returned no text)",
                              metadata={"handle": rec["handle"],
                                        "status": status,
                                        "result_chars": 0,
                                        "truncated": False})
        truncated = len(body) > _RESULT_INLINE_CHARS
        if truncated:
            omitted = len(body) - _RESULT_INLINE_CHARS
            output = (f"{body[:_RESULT_INLINE_CHARS]}\n\n"
                      f"… [{omitted} of {len(body)} chars omitted] …")
            path = self._persist(rec, body)
            if path:
                output += f"\n[full report saved: {path}]"
        else:
            output = body
        return ToolResult(
            success=True,
            output=output,
            metadata={"handle": rec["handle"], "status": status,
                      "result_chars": len(body), "truncated": truncated},
        )

    def _persist(self, rec: Dict[str, Any], body: str) -> Optional[str]:
        """Best-effort: keep the full (already redacted) report on disk.

        Reuses the spawn tool's writer, so background reports land beside the
        foreground ones with the same redaction and the same pruning schedule.
        """
        try:
            return self.spawn_tool._persist_raw_output(
                str(rec.get("child_id") or rec.get("handle") or "subagent"),
                str(rec.get("task_text") or ""),
                body,
            )
        except Exception:  # noqa: BLE001
            logger.debug("could not persist background sub-agent report",
                         exc_info=True)
            return None


from kairos.core.message_bus import Message  # noqa: E402  (after class for forward ref)