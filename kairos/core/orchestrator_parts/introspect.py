"""Mixin OrchIntrospectMixin — split from kairos/core/orchestrator.py."""
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




class OrchIntrospectMixin:
    def reload_skills(
        self, project_id: str, *, bundled_dir: Optional["Path"] = None,
        skip_bundled: bool = False,
    ) -> dict:
        """Manually trigger a skills re-discovery for *project_id*.

        The ``SkillsWatcher`` already polls mtimes once a second and
        will pick up changes automatically. This endpoint exists for
        the Settings drawer "Reload now" button and for tests: it
        forces an immediate ``SkillsLoader.discover()`` and reports
        the names it found.

        By default the loader also picks up the package-bundled
        skills (``kairos/skills/*.md``). Pass ``skip_bundled=True`` to
        scope the result to user + project skills only — useful for
        tests that want to assert against project-local skills only.

        Returns a dict with ``count`` and ``names``. If the project
        has no ``work_dir`` or no skills directory yet, returns
        ``count=0`` and an empty list.
        """
        project = self._projects.get(project_id)
        if not project:
            return {"count": 0, "names": [], "error": "project_not_found"}
        work_dir = getattr(project, "work_dir", None) or getattr(
            project, "workspace", None
        )
        if not work_dir:
            return {"count": 0, "names": [], "error": "no_work_dir"}
        try:
            from pathlib import Path
            from kairos.skills import SkillsLoader
            loader_kwargs: Dict[str, Any] = {"project_dir": Path(work_dir)}
            if skip_bundled:
                loader_kwargs["bundled_dir"] = SkillsLoader._SKIP_BUNDLED
            elif bundled_dir is not None:
                loader_kwargs["bundled_dir"] = bundled_dir
            loader = SkillsLoader(**loader_kwargs)
            skills = loader.discover()
            names = [s.name for s in skills]
            return {"count": len(names), "names": names}
        except Exception as e:  # noqa: BLE001
            logger.warning("reload_skills failed for %s: %s", project_id, e)
            return {"count": 0, "names": [], "error": str(e)}

    def get_plan_visualization(self, project_id: str) -> Optional[dict]:
        from kairos.review.mermaid import plan_to_mermaid, plan_to_file_tree
        plan = self.get_plan(project_id)
        if not plan or not plan.get("text"):
            return None
        text = plan["text"]
        try:
            mermaid = plan_to_mermaid(text)
            tree = plan_to_file_tree(text)
        except Exception:
            logger.debug("plan visualization failed", exc_info=True)
            return None
        return {"mermaid": mermaid, "file_tree": tree, "round": plan.get("round", 0)}

    def get_cache_stats(self, project_id: str) -> Optional[dict]:
        from kairos.tools.cache import get_cache
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return None
        return get_cache().stats()

    def get_round_comments(self, project_id: str,
                            round_no: int = 0) -> Optional[dict]:
        from kairos.review.comments import verdict_to_comments, comments_to_jsonl
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return None
        history = project.loop_session.history
        if not history:
            return None
        if round_no == 0:
            entry = history[-1]
            round_no = entry.get("round", 0)
        else:
            entry = next((h for h in history if h.get("round") == round_no), None)
            if entry is None:
                return None
        review = entry.get("review") or {}
        comments = verdict_to_comments(review, project_id=project_id, round_no=round_no)
        return {"round": round_no, "comments": comments, "jsonl": comments_to_jsonl(comments)}

    def list_preferences(self, project_id: str) -> list:
        if not self._db:
            return []
        return self._db.list_preferences(project_id)

    def revert_file(self, project_id: str, sha: str, path: str) -> tuple[bool, str]:
        """Restore one file to its state at the given checkpoint SHA."""
        project = self._projects.get(project_id)
        if not project:
            return False, f"Project not found: {project_id}"
        try:
            from kairos.tools.checkpoint import revert_file as _revert
        except ImportError:
            return False, "checkpoint tool not available"
        workspace = Path(getattr(project, "work_dir", None) or project.workspace)
        return _revert(workspace, sha, path)

    def add_preference(self, project_id: str, kind: str, rule: str) -> int:
        if not self._db:
            return 0
        return self._db.add_preference(project_id, kind, rule)

    def delete_preference(self, project_id: str, pref_id: int) -> bool:
        if not self._db:
            return False
        return self._db.delete_preference(project_id, pref_id)

    def get_pending_ask(self, project_id: str) -> Optional[dict]:
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return None
        s = project.loop_session
        if not s.ask_pending:
            return None
        return {"pending": True, "question": s.ask_question,
                "context": s.ask_context, "round": s.round}

    def answer_ask(self, project_id: str, answer: str) -> bool:
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return False
        project.loop_session.answer_ask(answer)
        return True

    def get_plan(self, project_id: str) -> Optional[dict]:
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return None
        s = project.loop_session
        from kairos.loop.review_loop import _sanitize_plan_text
        return {"pending": s.plan_pending, "decision": s.plan_decision,
                "text": _sanitize_plan_text(s.plan_text), "round": s.round}

    def refresh_all_agents(self):
        for agent_id, agent in self._agents.items():
            role = agent_id.split(".")[-1] if "." in agent_id else agent.role
            # Skip agents that are mid-run: swapping the provider under a
            # running loop mixes models mid-conversation and can close a
            # client that is still in use. Their next run picks up the new
            # config instead.
            lock = getattr(agent, "_lock", None)
            if lock is not None and lock.locked():
                logger.debug("Skipping model refresh for busy agent %s", agent_id)
                continue
            try:
                provider = self.model_router.get_provider_for_role(role)
                agent.refresh_model(provider.config)
            except Exception:
                logger.debug("Failed to refresh model for %s", agent_id, exc_info=True)

    def _load_yaml_prompts(self) -> Dict[str, str]:
        yaml_path = Path(__file__).parent.parent / "config" / "agents_config.yaml"
        prompts: Dict[str, str] = {}
        if yaml_path.exists():
            try:
                import yaml
                with open(yaml_path) as f:
                    cfg = yaml.safe_load(f) or {}
                for role_name, role_cfg in cfg.get("roles", {}).items():
                    if isinstance(role_cfg, dict) and "system_prompt" in role_cfg:
                        prompts[role_name] = role_cfg["system_prompt"]
            except Exception:
                logger.debug("Failed to load YAML prompts", exc_info=True)
            return prompts
