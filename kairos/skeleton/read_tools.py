"""Read-only tools for the general (skeleton) lane — and the bounded loop
that drives them.

Why this exists
---------------
The router (:mod:`kairos.task_router`) sends ordinary chat to the general lane
(``kairos/skeleton``). That lane's worker took a plain ``generate(prompt) -> str``
seam and had **no tools at all** (see :class:`kairos.skeleton.adapters.PromptWorker`),
so when the API folded an uploaded file into the prompt as an ``[附件]`` block
(``api/routes/projects.py:attachment_prompt_block``) the lane could not open it.
That asymmetry is exactly why the WeChat channel forced any message carrying
media back onto the Coder (``api/routes/weixin.py``). This module removes it: it
gives the general lane a *read-only* toolset, built from the very tools the
Coder/Reviewer already use, sandboxed to the same project root.

Safety is the Coder lane's, not a copy of it
--------------------------------------------
Every tool here is a real :mod:`kairos.tools` :class:`~kairos.tools.base.BaseTool`.
``BaseTool.__init_subclass__`` wraps each ``execute`` in the capability gate
(:func:`kairos.tools.base._capability_precheck` -> :func:`kairos.capabilities.assess`),
so a call is judged on capability + target **inside the tool** -- the
project-directory fence (``fence_path`` -> ``BaseTool._resolve_safe`` ->
:func:`kairos.tools.base.resolve_within_root`, which honours ``is_full_access()``)
and the zip-bomb guard (:mod:`kairos.tools.zipguard`, used by ``doc_read`` /
``xlsx_read``) all apply, unchanged. There is no second path policy here.

Read-only *by construction* and *by enforcement*: the toolset is built from
read-only tools only, and :func:`run_read_tool_loop` refuses -- before executing
-- any tool whose resolved capabilities are not a subset of
:data:`READ_ONLY_CAPABILITIES`. A write / execute / network tool can therefore
never be reached from this loop even if one were ever added to the list by
mistake. Nothing here writes a file into the user's workspace, runs a process,
or touches the network.
"""
from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from kairos.capabilities import Capability, resolve_capabilities
from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

#: The only capabilities the general lane may ever expose. Everything else --
#: ``WRITE_FILE``, ``EXEC_PROCESS``, ``NETWORK``, ``EXTERNAL_WRITE`` -- is
#: refused by :func:`run_read_tool_loop` before the tool runs.
READ_ONLY_CAPABILITIES = frozenset({Capability.READ_FILE})

#: How many model turns a single general-lane chat may spend on tools before it
#: is asked to answer. Small on purpose: opening the user's attachments is a
#: handful of reads, not a code session.
MAX_TOOL_TURNS = 6


def read_only_tools(root: Any) -> List[BaseTool]:
    """Build the general lane's read-only toolset, sandboxed to ``root``.

    The same tools the Coder/Reviewer are wired with (``orchestrator.py``), so
    a file that is readable there is readable here, through the identical
    sandbox. Only the readers are built -- no writer, no ``terminal``/``git``,
    no ``webfetch``/``browser``/``computer_use``.
    """
    # Imported lazily so importing this module never drags in a tool that a
    # trimmed install might not ship (mirroring how the orchestrator appends
    # optional tools in a try/except).
    from kairos.tools.data_analyze import DataAnalyzeTool
    from kairos.tools.doc_read import DocReadTool
    from kairos.tools.file_read import FileReadTool
    from kairos.tools.find import FindTool
    from kairos.tools.grep_tool import GrepTool
    from kairos.tools.xlsx_read import XlsxReadTool

    root = str(root)
    return [
        FileReadTool(allowed_root=root),    # text files (and dir listings)
        DocReadTool(allowed_root=root),     # .docx / .pptx
        XlsxReadTool(allowed_root=root),    # .xlsx
        DataAnalyzeTool(allowed_root=root),  # .csv / .tsv / delimited
        GrepTool(allowed_root=root),        # search text
        FindTool(allowed_root=root),        # locate files
    ]


def tool_names(tools: List[BaseTool]) -> List[str]:
    """The names of ``tools``, in order (for telemetry / assertions)."""
    return [getattr(t, "name", "") for t in tools]


def is_read_only(tool: Any) -> bool:
    """Whether ``tool`` declares only read-only capabilities.

    A tool that declares **nothing** is *not* read-only here (the gate is
    fail-closed, so an undeclared tool is not permitted either).
    """
    caps = resolve_capabilities(getattr(tool, "name", "") or "", obj=tool)
    if caps is None or not caps:
        return False
    return caps <= READ_ONLY_CAPABILITIES


def _parse_arguments(raw: Any) -> Dict[str, Any]:
    """Coerce a tool call's ``arguments`` into a ``dict`` (never raises)."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


async def _execute_read_tool(tools: List[BaseTool], tool_call: Any) -> ToolResult:
    """Run one model-proposed tool call, or refuse it with a readable result.

    Refusals the loop owns (an unknown tool name, a tool that is not read-only)
    come back as a failed :class:`ToolResult` the model reads and can react to,
    never as a crash. The tool's own ``execute`` is capability-gated, so the
    project-directory fence and the zip guard still apply inside it.
    """
    name = getattr(tool_call, "name", "") or ""
    tool = next((t for t in tools if getattr(t, "name", "") == name), None)
    if tool is None:
        return ToolResult(success=False, output="",
                          error=f"Unknown tool: {name}")
    if not is_read_only(tool):
        return ToolResult(
            success=False, output="",
            error=(f"Refused: {name} is not a read-only tool; the general lane "
                   f"may only read inside the project directory."))
    args = _parse_arguments(getattr(tool_call, "arguments", ""))
    try:
        return await tool.execute(**args)
    except Exception as exc:  # noqa: BLE001 - one readable line, not a crash
        return ToolResult(success=False, output="",
                          error=f"{name} failed: {type(exc).__name__}: {exc}")


async def run_read_tool_loop(
    *,
    client: Any,
    tools: List[BaseTool],
    system_prompt: str,
    message: str,
    max_turns: int = MAX_TOOL_TURNS,
) -> str:
    """Answer ``message`` on the general lane, letting the model read files.

    ``client`` is any object with ``async complete(messages, tools=...) ->
    response`` where ``response`` has ``content`` and (optionally) ``tool_calls``
    -- the exact contract the Coder's provider already implements
    (:class:`kairos.llm.base.BaseLLMProvider`), so the same model call does the
    same native function calling here.

    The loop is bounded: at most ``max_turns`` model turns, and every tool call
    is judged by :func:`_execute_read_tool` (read-only only). It returns the
    model's final text -- which the caller treats exactly like the prompt-only
    seam's answer (empty/None still means "no answer", so the caller can fall
    back to the Coder).
    """
    from kairos.llm.base import LLMMessage

    # The tool-result cache (kairos/tools/cache.py) is documented as
    # per-round and is a process-wide singleton. The loop runner clears it
    # every round; this lane never did, so entries survived across turns,
    # projects and the Coder's own rounds — that is how a read in project A
    # could answer a read in project B, and how a read after the Coder wrote
    # a file could return the pre-write contents. A chat turn is this lane's
    # round: start it clean.
    try:
        from kairos.tools.cache import clear_round
        clear_round()
    except Exception:  # noqa: BLE001
        pass

    schemas = []
    for tool in tools:
        if not is_read_only(tool):
            # Belt and braces: never advertise a non-read-only tool.
            continue
        try:
            schema = tool.to_schema()
        except Exception:  # noqa: BLE001 - a broken schema must not kill the turn
            continue
        schemas.append({
            "name": schema["name"],
            "description": schema.get("description", ""),
            "parameters": schema.get("parameters", {"type": "object",
                                                    "properties": {}}),
        })

    messages: List[Any] = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=message),
    ]

    if not schemas:
        # No usable read tool (all imports failed): degrade to a plain answer,
        # exactly the prompt-only seam the general lane had before.
        response = await client.complete(messages, tools=None)
        return "" if response is None else (getattr(response, "content", "") or "")

    last_text = ""
    for _ in range(max(1, int(max_turns))):
        response = await client.complete(messages, tools=schemas)
        if inspect.isawaitable(response):  # a client that is not a coroutine fn
            response = await response
        if response is None:
            break
        content = getattr(response, "content", "") or ""
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            return content
        last_text = content
        messages.append(LLMMessage(role="assistant", content=content,
                                   tool_calls=calls))
        for tool_call in calls:
            result = await _execute_read_tool(tools, tool_call)
            body = result.output if result.success else f"Error: {result.error}"
            messages.append(LLMMessage(
                role="tool",
                content=body,
                tool_call_id=getattr(tool_call, "id", "") or "",
                name=getattr(tool_call, "name", "") or "",
            ))
    # The budget ran out with the model still reading: return what it last
    # said (possibly empty), so the caller's fallback decision stays honest.
    return last_text


__all__ = [
    "READ_ONLY_CAPABILITIES",
    "MAX_TOOL_TURNS",
    "read_only_tools",
    "tool_names",
    "is_read_only",
    "run_read_tool_loop",
]
