"""The general chat lane as a *real* agent: the full toolset + its bounded loop.

Why this exists
---------------
The general lane (``kairos/skeleton``) answers ordinary web/IM chat. Until now
it was handed only the **read-only** toolset (:mod:`kairos.skeleton.read_tools`),
so the one thing a user actually wants from a chat -- "produce the file",
"run it", "go fetch that page" -- was impossible: the model could not write, run
a command or reach the network. This module gives the lane the *complete*
toolset and a loop that drives it, so a chat turn can do real work and hand back
what it produced.

Same tools, same fences -- not a second policy
----------------------------------------------
Every tool here is the very class the Coder is wired with (``kairos/core/
orchestrator.py``), built with the **same sandbox root** the Coder's file tools
use. ``BaseTool.__init_subclass__`` routes every ``execute`` through the
capability gate (:func:`kairos.capabilities.assess`), so the project-directory
fence (:func:`kairos.tools.base.resolve_within_root`, honouring
``is_full_access()``), the webfetch SSRF guard (:mod:`kairos.netsec`) and the
terminal's always-deny heads all apply unchanged. There is no second path
policy and no way around the gate: a call is judged inside the tool.

Read-only stays read-only
-------------------------
:mod:`kairos.skeleton.read_tools` is left exactly as it was -- a read-only
primitive with its own tested loop. This module is the general lane's toolset;
the two do not share a filter, so neither can weaken the other.

What is *not* here (deliberate)
-------------------------------
``browser`` / ``computer_use`` (``EXTERNAL_WRITE``), ``spawn_subagent`` and MCP
tools are absent, and the loop refuses -- before executing -- any tool whose
resolved capabilities are not a subset of :data:`GENERAL_TOOL_CAPABILITIES`.
That keeps the lane to read + write + edit + run + fetch, and leaves the
higher-blast-radius capabilities to the Coder lane.
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

#: The capabilities the general chat lane may ever expose: read, write, edit
#: (all ``WRITE_FILE``), run a process, and reach the network. ``EXTERNAL_WRITE``
#: (a browser / the desktop / an MCP server) is *not* here -- the loop refuses
#: such a tool even if one were added to the list by mistake.
GENERAL_TOOL_CAPABILITIES = frozenset({
    Capability.READ_FILE,
    Capability.WRITE_FILE,
    Capability.EXEC_PROCESS,
    Capability.NETWORK,
})

#: How many model turns a single general-lane chat may spend on tools.
#:
#: Raised from the read-only lane's 6: that budget was sized for "open the
#: attachment a user pasted" (a handful of reads). A real agent turn is a
#: *task* -- read the input, write the file, run it, fix it, answer -- and 6 is
#: routinely too few to finish one, which is exactly the "chat that can't
#: actually do anything" the lane is being fixed for. 12 is deliberately
#: modest: large enough for read -> write -> run -> verify -> answer with a
#: retry or two, small enough that a runaway model cannot spin. It is bounded
#: *together with* :data:`MAX_TOOL_RESULT_CHARS`, so the injected transcript
#: stays inside the model's context budget (see that constant).
GENERAL_CHAT_MAX_TURNS = 12

#: Cap on the characters of a single tool result written back into the model's
#: transcript. Without it, one ``terminal`` dump or a huge ``file_read`` repeated
#: across :data:`GENERAL_CHAT_MAX_TURNS` turns could push the window over its
#: budget -- the loop would keep calling the model with a transcript that never
#: fits. Individual tools already cap their own output; this is the belt-and-
#: braces ceiling at the loop, so the *loop* cannot be the thing that blows the
#: budget no matter what a tool returns.
MAX_TOOL_RESULT_CHARS = 40_000

#: Most produced files a single chat turn reports back.
MAX_CHAT_ARTIFACTS = 20
#: Largest produced file counted as an artifact (bigger files are still on disk;
#: they are just not offered for download).
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024

#: How many times a single general-lane chat may recover from a turn that
#: produced no visible answer AND called no tool. One extra turn is enough to
#: turn a thinking model's reasoning-only turn (empty content +
#: ``reply_from_reasoning``) -- or a turn that only *promises* to work -- into
#: the file the user asked for; capping it at one keeps a model that refuses to
#: act from spinning the loop.
GENERAL_CHAT_EMPTY_RECOVERIES = 1

#: The nudge appended (as a user turn) when a model turn ended with no content
#: and no tool call. It names the two honest outcomes -- act and hand back the
#: file, or answer -- so "I'll write a script" becomes a real ``file_write``.
GENERAL_CHAT_EMPTY_ANSWER_NUDGE = (
    "你上一条回复没有任何可见正文，也没有调用任何工具 —— 只是在内部思考或"
    "描述计划，用户什么也看不到。现在就给出结果：\n"
    "· 如果用户要的是文件 / 文档 / 报告 / 脚本，请立刻调用 file_write（或编辑"
    "类工具）把内容写进项目工作区，不要在回复里粘贴全文，也不要只打印计划或"
    "代码草稿；写完后在回复里给出文件路径。\n"
    "· 否则直接写出最终答复。"
)

#: How many times a single chat may be called out for *promising* work (a plan /
#: "I'll do it" / a pasted code draft) without actually calling a tool. Bounded
#: to one for the same reason as :data:`GENERAL_CHAT_EMPTY_RECOVERIES`.
GENERAL_CHAT_PROMISE_RECOVERIES = 1

#: The nudge appended when the model answered with a plan or a draft but called
#: no tool. "描述工作不是工作" is the whole point.
GENERAL_CHAT_PROMISE_NUDGE = (
    "你上一条回复只是描述了计划，或者贴了一段内容 / 代码草稿，并没有真正动手 —— "
    "用户要的是结果，不是计划。现在就执行：\n"
    "· 如果用户要的是文件 / 文档 / 报告 / 脚本，立刻调用 file_write（或编辑类"
    "工具）把内容写进项目工作区，然后在回复里只给出文件路径和一句说明，"
    "不要再把全文或代码粘进对话。\n"
    "· 否则直接给出最终答复。"
)


def general_tools(root: Any) -> List[BaseTool]:
    """Build the general lane's full toolset, sandboxed to ``root``.

    The same classes the Coder is wired with -- readers, writers/editors, the
    terminal and webfetch -- so a file a chat may touch is decided by the exact
    fence, gate and guards the Coder uses.
    """
    # Imported lazily (mirroring the read-only lane) so importing this module
    # never drags in a tool a trimmed install might not ship.
    from kairos.tools.data_analyze import DataAnalyzeTool
    from kairos.tools.doc_read import DocReadTool
    from kairos.tools.file_edit import (FileEditReplaceTool, FileEditTool,
                                        MultiEditTool)
    from kairos.tools.file_read import FileReadTool
    from kairos.tools.find import FindTool
    from kairos.tools.grep_tool import GrepTool
    from kairos.tools.terminal import TerminalTool
    from kairos.tools.webfetch import WebFetchTool
    from kairos.tools.xlsx_read import XlsxReadTool

    root = str(root)
    return [
        # readers (unchanged from the read-only lane)
        FileReadTool(allowed_root=root),     # text files (and dir listings)
        DocReadTool(allowed_root=root),      # .docx / .pptx
        XlsxReadTool(allowed_root=root),     # .xlsx
        DataAnalyzeTool(allowed_root=root),  # .csv / .tsv / delimited
        GrepTool(allowed_root=root),         # search text
        FindTool(allowed_root=root),         # locate files
        # writers / editors -- inside the same root, gated the same way
        FileEditTool(allowed_root=root),         # create / overwrite a file
        FileEditReplaceTool(allowed_root=root),  # replace text in a file
        MultiEditTool(allowed_root=root),        # many edits across files
        # run a command, sandboxed to the same root (cwd)
        TerminalTool(allowed_cwd=root),
        # fetch a public URL (SSRF-guarded)
        WebFetchTool(allowed_root=root),
    ]


def tool_names(tools: List[BaseTool]) -> List[str]:
    """The names of ``tools``, in order (for telemetry / assertions)."""
    return [getattr(t, "name", "") for t in tools]


def is_allowed(tool: Any) -> bool:
    """Whether ``tool`` declares only capabilities the general lane may expose.

    An undeclared tool is *not* allowed (the gate is fail-closed, so an
    undeclared tool is not permitted either).
    """
    caps = resolve_capabilities(getattr(tool, "name", "") or "", obj=tool)
    if caps is None or not caps:
        return False
    return caps <= GENERAL_TOOL_CAPABILITIES


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


def _clip(text: str) -> str:
    """Bound one tool result before it enters the transcript (context guard)."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return (text[:MAX_TOOL_RESULT_CHARS]
            + f"\n... [truncated: {len(text) - MAX_TOOL_RESULT_CHARS} more "
              f"characters not shown]")


async def _execute_general_tool(tools: List[BaseTool],
                                tool_call: Any) -> ToolResult:
    """Run one model-proposed tool call, or refuse it with a readable result.

    Refusals the loop owns (an unknown name, a tool outside
    :data:`GENERAL_TOOL_CAPABILITIES`) come back as a failed
    :class:`ToolResult` the model reads and can react to -- never a crash. The
    tool's own ``execute`` is capability-gated, so the path fence, the network
    guard and the terminal's red lines still apply inside it.
    """
    name = getattr(tool_call, "name", "") or ""
    tool = next((t for t in tools if getattr(t, "name", "") == name), None)
    if tool is None:
        return ToolResult(success=False, output="",
                          error=f"Unknown tool: {name}")
    if not is_allowed(tool):
        return ToolResult(
            success=False, output="",
            error=(f"Refused: {name} is outside the general lane's capability "
                   f"set (read / write / run / fetch only)."))
    args = _parse_arguments(getattr(tool_call, "arguments", ""))
    try:
        return await tool.execute(**args)
    except Exception as exc:  # noqa: BLE001 - one readable line, not a crash
        return ToolResult(success=False, output="",
                          error=f"{name} failed: {type(exc).__name__}: {exc}")


async def run_general_tool_loop(
    *,
    client: Any,
    tools: List[BaseTool],
    system_prompt: str,
    message: str,
    max_turns: int = GENERAL_CHAT_MAX_TURNS,
) -> str:
    """Answer ``message`` on the general lane, letting the model act.

    ``client`` is any object with ``async complete(messages, tools=...) ->
    response`` where ``response`` has ``content`` and (optionally) ``tool_calls``
    -- the exact contract the Coder's provider implements
    (:class:`kairos.llm.base.BaseLLMProvider`), so the same model call does the
    same native function calling here.

    The loop is bounded: at most ``max_turns`` model turns, each tool result
    clipped to :data:`MAX_TOOL_RESULT_CHARS`, and every tool call judged by
    :func:`is_allowed` (read / write / run / fetch only). Returns the model's
    final text -- which the caller treats exactly like the prompt-only seam's
    answer (empty/None still means "no answer", so the caller can fall back).
    """
    from kairos.llm.base import LLMMessage

    # The tool-result cache (kairos/tools/cache.py) is a process-wide singleton
    # documented as per-round. A chat turn is this lane's round: start it clean,
    # so a read after a write returns fresh bytes and one project's read can
    # never answer another's.
    try:
        from kairos.tools.cache import clear_round
        clear_round()
    except Exception:  # noqa: BLE001 - a cache hiccup must not kill the turn
        logger.debug("general lane: could not clear the round cache",
                     exc_info=True)

    schemas = []
    for tool in tools:
        if not is_allowed(tool):
            # Belt and braces: never advertise a tool the loop would refuse.
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
        # No usable tool (all imports failed): degrade to a plain answer.
        response = await client.complete(messages, tools=None)
        return "" if response is None else (getattr(response, "content", "") or "")

    # The "promised but didn't act" detector is the Coder lane's own -- one
    # source of truth for what counts as a first-person commitment. Imported
    # lazily so the skeleton lane never drags the agent stack in at import time;
    # if it is unavailable the promise nudge simply never fires.
    try:
        from kairos.agents.base import (FOLLOW_THROUGH_MARKERS,
                                        _promises_action)
    except Exception:  # pragma: no cover - degrade to "no promise detected"
        FOLLOW_THROUGH_MARKERS = ()
        def _promises_action(_text: str) -> bool:  # type: ignore[misc]
            return False

    def _looks_like_a_promise_or_draft(text: str) -> bool:
        """True when a reply announces work / pastes a draft instead of doing it."""
        if not text:
            return False
        if "```" in text:                       # a code/content draft
            return True
        if any(m in text.lower() for m in FOLLOW_THROUGH_MARKERS):
            return True
        return _promises_action(text)

    last_text = ""
    empty_recoveries = 0
    promise_recoveries = 0
    any_tool_call = False
    for _ in range(max(1, int(max_turns))):
        response = await client.complete(messages, tools=schemas)
        if inspect.isawaitable(response):  # a client that is not a coroutine fn
            response = await response
        if response is None:
            break
        content = getattr(response, "content", "") or ""
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            text = content.strip()
            if not text and empty_recoveries < GENERAL_CHAT_EMPTY_RECOVERIES:
                # The model stopped without saying anything AND without acting:
                # the exact shape a thinking model produces when it spends the
                # turn on hidden reasoning (``reply_from_reasoning``). Returning
                # that empty text is how the user ends up with no answer and no
                # file -- so ask once, explicitly, for the result. This is the
                # decidable recovery: the WARNING below records that the lane
                # had to nudge.
                empty_recoveries += 1
                logger.warning(
                    "general lane: empty reply with no tool call "
                    "(model=%s, from_reasoning=%s); asking once for the result",
                    getattr(response, "model", ""),
                    bool(getattr(response, "reply_from_reasoning", False)),
                )
                messages.append(LLMMessage(
                    role="user", content=GENERAL_CHAT_EMPTY_ANSWER_NUDGE))
                continue
            if (text and not any_tool_call
                    and promise_recoveries < GENERAL_CHAT_PROMISE_RECOVERIES
                    and _looks_like_a_promise_or_draft(text)):
                # The model answered with a plan / "I'll do it" / a pasted draft
                # and called no tool: describing the work is not doing it. Ask
                # once, out loud, for the actual result.
                promise_recoveries += 1
                logger.warning(
                    "general lane: reply only promised a plan/draft and called "
                    "no tool (model=%s); holding it to the work",
                    getattr(response, "model", ""),
                )
                messages.append(LLMMessage(
                    role="user", content=GENERAL_CHAT_PROMISE_NUDGE))
                continue
            return content
        any_tool_call = True
        last_text = content
        messages.append(LLMMessage(role="assistant", content=content,
                                   tool_calls=calls))
        for tool_call in calls:
            result = await _execute_general_tool(tools, tool_call)
            body = result.output if result.success else f"Error: {result.error}"
            messages.append(LLMMessage(
                role="tool",
                content=_clip(body),
                tool_call_id=getattr(tool_call, "id", "") or "",
                name=getattr(tool_call, "name", "") or "",
            ))
    # The budget ran out with the model still working: return what it last
    # said (possibly empty), so the caller's fallback decision stays honest.
    return last_text


# ---------------------------------------------------------------------------
# Artifacts: what this turn actually produced
# ---------------------------------------------------------------------------


def _guess_mime(name: str) -> str:
    """Mime type for a produced file, from the shared neutral table.

    One source of truth (:mod:`kairos.mime_guess`): the same helper the
    ``[附件]`` block and the download endpoint use, so the entry the frontend
    gets and the ``Content-Type`` it downloads are never in disagreement. It is
    a plain, synchronous import -- the skeleton must never reach into the API
    layer here, because importing that from inside a running event loop
    deadlocks.
    """
    from kairos.mime_guess import guess_mime
    return guess_mime(name)


def collect_artifacts(
    root: Any,
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    *,
    max_files: int = MAX_CHAT_ARTIFACTS,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> List[Dict[str, Any]]:
    """The files this turn **created or changed**, as delivery entries.

    Built from the snapshot pair (:func:`kairos.file_snapshot.diff_touched_files`)
    -- the neutral mechanism the WeChat channel already uses -- so the answer to
    "what did this turn produce?" is the file system's, not the model's claim.

    Each entry is frozen to four fields: ``path`` (project-relative, posix),
    ``name`` (basename), ``size`` (int bytes) and ``mime`` (str). A touched file
    that resolves outside ``root`` (a symlink that escapes the project, a path
    from a sibling checkout) is **dropped**, files over ``max_bytes`` are
    dropped, and the list is capped at ``max_files`` -- so an entry is always a
    safe, download-under-``root`` target.
    """
    result: List[Dict[str, Any]] = []
    try:
        root_path = Path(str(root)).resolve()
    except (OSError, ValueError):
        return result
    from kairos.file_snapshot import diff_touched_files

    for raw in diff_touched_files(before, after):
        try:
            resolved = Path(raw).resolve()
            rel = resolved.relative_to(root_path)
        except (OSError, ValueError):
            continue                      # outside the project root -> not ours
        try:
            if not resolved.is_file():
                continue
            size = resolved.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            continue
        result.append({
            "path": rel.as_posix(),
            "name": resolved.name,
            "size": int(size),
            "mime": _guess_mime(resolved.name),
        })
        if len(result) >= max_files:
            break
    return result


__all__ = [
    "GENERAL_TOOL_CAPABILITIES",
    "GENERAL_CHAT_MAX_TURNS",
    "GENERAL_CHAT_EMPTY_RECOVERIES",
    "GENERAL_CHAT_EMPTY_ANSWER_NUDGE",
    "GENERAL_CHAT_PROMISE_RECOVERIES",
    "GENERAL_CHAT_PROMISE_NUDGE",
    "MAX_TOOL_RESULT_CHARS",
    "MAX_CHAT_ARTIFACTS",
    "MAX_ARTIFACT_BYTES",
    "general_tools",
    "tool_names",
    "is_allowed",
    "run_general_tool_loop",
    "collect_artifacts",
]
