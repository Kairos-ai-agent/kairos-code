"""Mixin OrchLoopControlMixin — split from kairos/core/orchestrator.py."""
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




class OrchLoopControlMixin:
    def stop_loop(self, project_id: str) -> bool:
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return False
        project.loop_session.request_stop()
        project.loop_session.reject_plan()
        # Also cancel the loop task so "stop" takes effect immediately,
        # instead of waiting for the current Coder/Reviewer round to hit
        # its PER_ROUND_TIMEOUT_S (up to 600s). _on_loop_done handles a
        # cancelled task and marks the project "stopped".
        task = getattr(project, "loop_task", None)
        if task is not None and not task.done():
            task.cancel()
        return True

    def approve_plan(self, project_id: str, plan_text: str = "") -> bool:
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return False
        project.loop_session.approve_plan(plan_text)
        return True

    def reject_plan(self, project_id: str) -> bool:
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return False
        project.loop_session.reject_plan()
        return True

    def _on_loop_done(self, project_id: str, task: asyncio.Task):
        project = self._projects.get(project_id)
        if not project:
            return
        if task.cancelled():
            project.status = "stopped"
        elif task.exception():
            project.status = "failed"
            logger.exception("Loop for %s raised", project_id, exc_info=task.exception())
        else:
            session = project.loop_session
            project.status = "stopped" if (session and session.user_stopped) else "done"
        try:
            self._db.save_project(project)
        except Exception:
            logger.debug("Failed to persist final status for %s",
                         project_id, exc_info=True)

        # Self-learning: fire-and-forget consolidation so the agent
        # gets smarter in the background while the user reads the result.
        # Wrapped in try/except so a post-loop error never blocks the
        # status save above.
        try:
            rounds = self._db.load_loop_rounds(project_id, limit=20)
            if rounds and rounds[-1].get("approve"):
                # Cheap, no-LLM pass: re-derive advisory, promote
                # repeated failures to preferences, flag ambiguous
                # signatures. Runs synchronously so the next loop on
                # this project immediately benefits.
                from kairos.memory.growth import consolidate_project
                summary = consolidate_project(self._db, project_id, rounds)
                if summary.get("promoted_preferences"):
                    logger.info("self-learning: promoted %d preference(s) for %s",
                                summary["promoted_preferences"], project_id)
                # Heavy LLM-driven reflection runs as a background
                # task. It writes project_notes + project_skills. We
                # fire it but never await it; a future start_loop
                # sees the writes when it pulls the memory block.
                self._maybe_reflect(project_id, rounds)
        except Exception:
            logger.debug("self-learning post-loop pass failed", exc_info=True)

    def _maybe_reflect(self, project_id: str, rounds: List[dict]) -> None:
        """Schedule an LLM-driven self-reflection in the background.

        Cheap heuristic for whether reflection is worth it: only run
        when (a) at least 5 rounds happened, (b) at least one round
        was approved (we have something to learn from), and (c) the
        previous reflection for this project is more than 30 minutes
        old (so we do not hammer the LLM on tiny projects). The
        reflection itself is implemented in `kairos.learning.reflect`
        and is wrapped in an asyncio.Task so it never blocks.
        """
        approved = [r for r in rounds if r.get("approve")]
        if len(rounds) < 5 or not approved:
            return
        try:
            from kairos.learning.reflect import maybe_run_reflection
            task = asyncio.create_task(
                maybe_run_reflection(self._db, project_id, rounds),
                name=f"reflect-{project_id}",
            )
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)
        except Exception:
            logger.debug("reflection scheduling failed", exc_info=True)
