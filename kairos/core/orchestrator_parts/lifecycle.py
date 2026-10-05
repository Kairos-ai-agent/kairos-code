"""Mixin OrchLifecycleMixin — split from kairos/core/orchestrator.py."""
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




class OrchLifecycleMixin:
    def delete_project(self, project_id: str):
        """Soft-delete (R38.6): archive the project in the DB and
        tear down its in-memory runtime.

        The DB row, sessions, files, and notes are preserved
        (``archived_at`` is set, no DELETE). The user can restore
        via ``restore_project()``. The in-memory ``_projects`` map
        is cleared so the UI no longer sees the project (until
        Kairos restarts and re-loads from the DB — at which point
        ``load_projects()`` filters out archived rows by default).

        We do NOT call ``self._db.delete_project_memory()`` — that
        would wipe the sessions/files we just preserved for restore.
        """
        project = self._projects.pop(project_id, None)
        if not project:
            return
        if project.loop_task and not project.loop_task.done():
            project.loop_task.cancel()
        # Archive the row (sets archived_at). Records, sessions,
        # files, and notes are preserved.
        if hasattr(self._db, 'archive_project'):
            self._db.archive_project(project_id)
        for agent_id in [project.coder.agent_id if project.coder else None,
                          project.reviewer.agent_id if project.reviewer else None]:
            if agent_id:
                self._agents.pop(agent_id, None)
        # Tear down per-project runtime resources.
        self._close_project_runtime(project)

    def restore_project(self, project_id: str) -> bool:
        """Reverse an archive: clear ``archived_at`` so the project
        shows up in ``load_projects()`` again. R38.6."""
        if not hasattr(self._db, 'restore_project'):
            return False
        return self._db.restore_project(project_id)

    async def close(self) -> None:
        """Tear down the orchestrator: stop every project's runtime.

        Called from FastAPI's lifespan shutdown. Idempotent — calling
        twice is a no-op.
        """
        # First cancel any in-flight loop tasks so they stop awaiting the
        # LLM clients we're about to close below (otherwise a background
        # loop can keep holding an already-closed HTTP client). Brief await
        # so the cancel lands; best-effort on timeout.
        for project in list(self._projects.values()):
            task = getattr(project, "loop_task", None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(task, return_exceptions=True), timeout=5
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
        for project in list(self._projects.values()):
            # Sync cleanup first (watchers, worktrees), then async
            # (MCP subprocesses).
            self._close_project_runtime(project)
            await self._close_project_runtime_async(project)
        for agent in list(self._agents.values()):
            try:
                await agent._llm.close()
            except Exception:
                pass

    def close_sync(self) -> None:
        """Sync counterpart of `close()` for non-async callers
        (e.g. tests, CLI shutdown). Best-effort: any async-only
        resources (MCP) are skipped here."""
        for project in list(self._projects.values()):
            self._close_project_runtime(project)

    def _is_new_project(self, project_id: str) -> bool:
        """True when the project has no prior sessions.

        Round 37: drives the "new project bootstrap" behavior in
        ``start_loop()`` (unbounded cap + bug-only reviewer). We
        probe the messages + loop_rounds tables (the canonical
        source of truth — there's no dedicated sessions table)
        and treat "no rows" as "new project".

        **Conservative default: False on any error.** If the DB
        is down or the query fails for any reason, we assume
        the project is NOT new. This avoids accidentally
        applying the unbounded cap to a project that may
        actually have prior history we can't see right now.
        """
        try:
            msgs = self._db.load_messages(limit=1, project_id=project_id) or []
            rounds = self._db.load_loop_rounds(project_id, limit=1) or []
        except Exception:
            logger.debug("_is_new_project query failed; defaulting to False",
                         exc_info=True)
            return False
        return len(msgs) == 0 and len(rounds) == 0
