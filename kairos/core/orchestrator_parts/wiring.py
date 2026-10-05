"""Mixin OrchWiringMixin — split from kairos/core/orchestrator.py."""
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

_VALID_SPECIALISTS = {
    "security_reviewer", "perf_reviewer",
    "design_reviewer", "test_reviewer",
    "docs_reviewer", "refactor_reviewer",
}


class OrchWiringMixin:
    def _create_role_worktrees(self, work_dir: str):
        """Create per-role worktrees if `work_dir` is inside a git repo.

        Returns (coder_wt, reviewer_wt). Each is a Worktree or None.
        Raises on hard failure (git missing, malformed repo) — caller
        logs and continues.

        Set ``KAIROS_SKIP_WORKTREES=1`` to disable worktree isolation
        entirely (e.g. on machines where git checkout is pathologically
        slow due to antivirus scanning — a 100s checkout per worktree
        otherwise stalls backend startup).
        """
        if os.environ.get("KAIROS_SKIP_WORKTREES", "").strip().lower() \
                in ("1", "true", "yes", "on"):
            return (None, None)
        from kairos.worktree import WorktreeManager
        if not work_dir:
            return (None, None)
        try:
            mgr = WorktreeManager(repo_path=Path(work_dir))
        except Exception:
            # Not a git repo (or no git installed) — silently skip.
            return (None, None)
        coder_wt = mgr.create(branch_name=WorktreeManager.unique_branch_name("coder"))
        reviewer_wt = mgr.create(branch_name=WorktreeManager.unique_branch_name("reviewer"))
        return (coder_wt, reviewer_wt)

    def _instantiate_specialists(self, project_id: str,
                                specialist_names: List[str]) -> List[Any]:
        """Create one agent instance per requested specialist role.

        Returns an empty list when the project has no specialist set
        configured, when the model router is offline, or when any
        specialist class fails to import. Specialists run alongside
        the main Reviewer; their scores are weighted-averaged.
        """
        if not specialist_names:
            return []
        out: List[Any] = []
        for name in specialist_names:
            if name not in _VALID_SPECIALISTS:
                continue
            cls = self._resolve_specialist_class(name)
            if cls is None:
                continue
            try:
                agent = self._make_agent(
                    project_id, name, cls,
                    self.model_router.get_provider_for_role(name),
                    [],
                    self.message_bus,
                    self._load_yaml_prompts(),
                )
                out.append(agent)
            except Exception:
                logger.exception("failed to create specialist %s", name)
        return out

    def _resolve_specialist_class(self, name: str):
        cached = self._SPECIALIST_CLASSES.get(name)
        if cached or cached is False:
            return cached or None
        try:
            if name == "security_reviewer":
                from kairos.agents.roles import SecurityReviewer
                cls = SecurityReviewer
            elif name == "perf_reviewer":
                from kairos.agents.roles import PerfReviewer
                cls = PerfReviewer
            elif name == "design_reviewer":
                from kairos.agents.roles import DesignReviewer
                cls = DesignReviewer
            elif name == "test_reviewer":
                from kairos.agents.roles import TestReviewer
                cls = TestReviewer
            elif name == "docs_reviewer":
                from kairos.agents.roles import DocsReviewer
                cls = DocsReviewer
            elif name == "refactor_reviewer":
                from kairos.agents.roles import RefactorReviewer
                cls = RefactorReviewer
            else:
                cls = None
        except Exception:
            logger.exception("could not import specialist class %s", name)
            cls = None
        self._SPECIALIST_CLASSES[name] = cls or False
        return cls

    def _make_agent(self, project_id, role, role_cls, provider, tools,
                    bus, yaml_prompts) -> KairosAgent:
        agent_id = f"{project_id}.{role}"
        kwargs = {}
        if role in yaml_prompts:
            kwargs["system_prompt"] = yaml_prompts[role]
        # Pass the project's work_dir so the agent can pick up AGENTS.md
        # and Skills from the project. `getattr` keeps this method usable
        # from tests that bypass __init__ (where self._projects may not
        # have been set up).
        projects_map = getattr(self, "_projects", None)
        if projects_map is not None:
            project = projects_map.get(project_id)
            if project is not None:
                kwargs["project_dir"] = str(
                    project.work_dir or project.workspace
                )
        agent = role_cls(
            agent_id=agent_id,
            llm_config=provider.config,
            message_bus=bus,
            tools=tools,
            **kwargs,
        )
        for tool in tools:
            if tool.name == "spawn_subagent":
                tool.parent_agent = agent
                tool.project_id = project_id
        self._agents[agent_id] = agent
        return agent
