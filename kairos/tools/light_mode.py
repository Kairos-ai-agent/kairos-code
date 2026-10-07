"""Light tool mode for small local models.

A cloud model with a 200k window can afford the whole tool catalogue. A local
model served by LM Studio / Ollama behind an 8k window cannot: the ~22 tool
schemas are the single largest block of the prompt the agent sends (the very
block that pushed a real request to 8208 tokens and made the server answer
``request (8208 tokens) exceeds the available context size (8192 tokens)``), and
a 4B model almost never elects to call any of the long tail anyway.

Light mode narrows the tool SCHEMAS advertised to the model to five core tools
that cover the read / write / run loop. The tools stay wired on the agent (a
call that arrives for one of them is still dispatched); only what the model is
told about is reduced, because the schemas are the cost this exists to remove.

Nothing here changes behaviour unless a provider explicitly opts in
(``LLMConfig.light_tools = True``) or its window is small enough that
:func:`is_light_mode` infers it.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

#: The five tools a code agent cannot work without. Chosen to cover the loop:
#: read a file, write a file, patch a file, run a command, search the tree.
CORE_TOOL_NAMES: frozenset[str] = frozenset({
    "file_read",
    "file_write",
    "file_edit_replace",
    "terminal",
    "grep",
})

#: A window at or below this is treated as "small" and gets light mode even
#: without an explicit flag. 8192 (LM Studio's default) is the case that
#: motivated this; 16384 is the next common local setting.
SMALL_WINDOW_TOKENS = 16384


def is_light_mode(llm_config: Any) -> bool:
    """Whether the request should advertise only :data:`CORE_TOOL_NAMES`.

    Explicit ``light_tools`` on the config wins in both directions (True opts
    in, False opts out even under a small window). With no flag, a known
    ``context_window`` at or below :data:`SMALL_WINDOW_TOKENS` infers light
    mode. An unknown window and no flag leaves the full catalogue in place —
    the historical behaviour, unchanged for cloud models.
    """
    if llm_config is None:
        return False
    flag = getattr(llm_config, "light_tools", None)
    if flag is not None:
        return bool(flag)
    window = getattr(llm_config, "context_window", None)
    try:
        window = int(window) if window else 0
    except (TypeError, ValueError):
        window = 0
    return 0 < window <= SMALL_WINDOW_TOKENS


def select_light_tools(tools: Optional[Sequence[Any]]) -> list[Any]:
    """The subset of *tools* whose schema is worth sending in light mode.

    Matches on the tool's ``name`` attribute (or a dict's ``name`` key, for
    callers that carry schemas rather than tool objects). Order is preserved
    so the catalogue the model sees stays stable across turns.
    """
    if not tools:
        return []
    out = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", "")
        if name in CORE_TOOL_NAMES:
            out.append(tool)
    return out


def core_names_present(tools: Optional[Iterable[Any]]) -> list[str]:
    """The core tool names actually present in *tools* (for telemetry/tests)."""
    return [t if isinstance(t, str) else
            (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))
            for t in (tools or [])]
