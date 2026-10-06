"""Mixin AgentChatMixin — split from kairos/agents/base.py."""
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
from kairos.agents.identity import KAIROS_IDENTITY
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

# The agent used to stop three turns in a row asking the same question
# ("commit these files first, or continue?") while the user was typing 继续做 --
# the answer was already on the screen. Stated in both prompt branches.
ACT_DONT_ASK_DIRECTIVE = (
    "### 怎么干活\n"
    "- 用户的指令就是授权。他说了「继续」「直接做」「按你的判断做」，那就是许可："
    "执行，然后汇报结果。\n"
    "- 同一个问题不要问第二遍。问过一次、用户答过了，这个问题就关闭了——即使你心里"
    "还不确定，也按他给的方向做，并在汇报里说明你的假设。\n"
    "- 文档里给了多个可选范围时（例如「做 §1，或 §1–§3，视时间而定」），不要回头问"
    "用户选哪个：挑能覆盖后续步骤的那个（超集，例如 §1–§3）直接做，并在结尾总结里"
    "一句话说明你选了哪个范围。\n"
    "- 先自己查：读文件、跑只读命令、看 git 状态。只有答案确实不在磁盘上、"
    "且动作不可逆时才开口问。\n"
    "- 汇报时说清楚改了哪些文件、结果是什么，而不是你考虑过什么。"
)




class AgentChatMixin:
    async def chat(self, message: str, *, voice_mode: bool = False) -> str:
        """Direct chat with this agent (for UI interaction).

        ``voice_mode`` tells the agent its answer will be read aloud, so it
        writes for the ear — short, no Markdown — instead of leaving the
        interface to trim a wall of text into something sayable.
        """
        async with self._lock:
            return await self._chat_impl(message, voice_mode=voice_mode)

    async def agenerate(self, prompt: str) -> str:
        """Single-shot completion with NO memory/tool side effects.

        Used by post-loop self-reflection (``kairos.reflection.run_reflection``
        looks for ``agenerate``/``generate``). Deliberately bypasses
        ``self._memory`` and the tool loop so reflection can't pollute the
        agent's conversation state. Returns "" on failure (never raises).
        """
        try:
            resp = await self._llm.complete(
                [LLMMessage(role="user", content=prompt)]
            )
            return resp.content or ""
        except Exception:  # noqa: BLE001
            logger.debug("agenerate failed", exc_info=True)
            return ""

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
            return (KAIROS_IDENTITY + " Respond "
                    "conversationally to the user's message. Use "
                    "tools when helpful.\n" + ACT_DONT_ASK_DIRECTIVE)
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
            # "You are the Coder for project X" made the model think its
            # *identity* was a Coder environment (it answered "I am Claude,
            # running in a Coder environment"). It is a role in this project,
            # not who it is — the identity is stated in ``base`` above.
            head = f'You are working as the Coder for project {name}.'
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
            # working_fixes rows: the old path read ``db.conn`` — a
            # Persistence has no such attribute — and selected columns that
            # do not exist, so it raised AttributeError on every call and was
            # swallowed; the "Known issues to avoid" block never appeared.
            # Use the real accessor and the real schema instead.
            try:
                wf_rows = db.list_working_fixes(self.project_id, limit=5)
            except Exception:
                logger.debug("chat prompt: list_working_fixes failed", exc_info=True)
                wf_rows = []
            if wf_rows:
                lines = ["### Known issues to avoid"]
                for row in wf_rows:
                    sev = (row.get("issue_category") or "general")
                    sig = (row.get("from_signature") or "")[:80]
                    fix = (row.get("fix_body") or "")[:120]
                    lines.append(f"- [{sev}] {sig} — fix: {fix}")
                blocks.append("\n".join(lines))

        blocks.append(ACT_DONT_ASK_DIRECTIVE)

        # 4) assemble
        base = (KAIROS_IDENTITY + " Respond "
                "conversationally to the user's message. Use "
                "tools when helpful.")
        if blocks:
            ctx = " ".join(blocks)
            return (f"{base}\n\n"
                    f"You have the following project context:\n"
                    f"{ctx}")
        return base
