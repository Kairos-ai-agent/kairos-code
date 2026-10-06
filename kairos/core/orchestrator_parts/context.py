"""Mixin OrchContextMixin — split from kairos/core/orchestrator.py."""
from __future__ import annotations
import asyncio
import dataclasses
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import importlib as _il
from kairos.core.message_bus import Message, MessageBus
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter
from kairos.tools.base import ToolResult
from kairos.tools.code_search import CodeSearchTool
from kairos.tools.file_edit import FileEditReplaceTool, FileEditTool, MultiEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.find import FindTool
from kairos.tools.git_tool import GitTool
from kairos.tools.grep_tool import GrepTool
from kairos.tools.subagent import SubagentTool
from kairos.tools.terminal import TerminalTool
from kairos.tools.webfetch import WebFetchTool, WebSearchTool

logger = logging.getLogger(__name__)




class OrchContextMixin:
    def build_preferences_block(self, project_id: str) -> str:
        prefs = self._db.list_preferences(project_id)
        if not prefs:
            return ""
        by_kind = {"always": [], "never": [], "prefer": []}
        for p in prefs:
            kind = p.get("kind", "always")
            rule = (p.get("rule") or "").strip()
            if kind in by_kind and rule:
                by_kind[kind].append(rule)
        lines = ["## Project Conventions (user-saved rules)"]
        if by_kind["always"]:
            lines.append("")
            lines.append("Always:")
            for r in by_kind["always"]:
                lines.append(f"- {r}")
        if by_kind["never"]:
            lines.append("")
            lines.append("Never:")
            for r in by_kind["never"]:
                lines.append(f"- {r}")
        if by_kind["prefer"]:
            lines.append("")
            lines.append("Prefer:")
            for r in by_kind["prefer"]:
                lines.append(f"- {r}")
        return "\n".join(lines)

    def get_all_agent_states(self, project_id: Optional[str] = None) -> List[dict]:
        agents = self._agents.values()
        if project_id:
            project = self._projects.get(project_id)
            if project:
                agents = [a for a in agents if a.agent_id.startswith(project_id + ".")]
        return [a.state.model_dump() for a in agents]

    def get_message_history(self, limit: int = 100,
                             project_id: Optional[str] = None) -> List[dict]:
        return [m.to_dict() for m in
                self.message_bus.get_history(limit=limit, project_id=project_id)]

    def build_memory_block(self, project_id: str, requirement: str,
                            last_failure_signature: Optional[str] = None,
                            last_reviewer_comments: Optional[List[dict]] = None) -> str:
        """Assemble the memory section that goes in front of the user
        requirement. Pulls notes, skills, working fixes, FTS-ranked
        history, reviewer comments, ask history, and global KB
        insights into one bounded string. See kairos.memory.retrieval.

        `last_failure_signature` / `last_reviewer_comments` are
        passed through so the next-round prompt can short-circuit
        on a known-bad pattern. Both are optional.
        """
        try:
            from kairos.memory.retrieval import assemble_coder_memory
        except Exception:
            logger.debug("memory module import failed", exc_info=True)
            return ""
        try:
            return assemble_coder_memory(
                self._db, project_id, requirement,
                last_failure_signature=last_failure_signature,
                last_reviewer_comments=last_reviewer_comments,
            )
        except Exception:
            logger.debug("assemble_coder_memory failed", exc_info=True)
            return ""

    def _persist_message(self, msg):
        # Mid-stream `agent.thinking` hints drive the live rolling line only:
        # one turn can emit dozens (throttled, but still many), and persisting
        # them would flood the DB and the thread a refresh reloads. The closing
        # hint (no `transient` flag) is the one that survives.
        if (msg.metadata or {}).get("transient"):
            return
        try:
            self._db.save_message(msg)
        except Exception:
            logger.debug("Failed to persist message: %s", msg.topic, exc_info=True)
