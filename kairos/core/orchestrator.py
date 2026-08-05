"""Orchestrator - LoopReview mode.

Manages projects and runs the Coder <-> Reviewer loop. Each project gets:
- one Coder agent (universal, full tools)
- one Reviewer agent (read-only + tests, grades every round)

The orchestrator itself is thin: it just creates the agents and starts the
loop. The loop logic lives in kairos.loop.loop_runner (re-exported via
kairos.loop.review_loop for backward compatibility).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from kairos.agents.base import AgentTask, KairosAgent
from kairos.agents.roles import Coder, Reviewer
from kairos.core.message_bus import Message, MessageBus
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter
from kairos.tools.base import ToolResult
from kairos.tools.file_edit import FileEditReplaceTool, FileEditTool, MultiEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.find import FindTool
from kairos.tools.git_tool import GitTool
from kairos.tools.grep_tool import GrepTool
from kairos.tools.subagent import SubagentTool
from kairos.tools.terminal import TerminalTool
from kairos.tools.webfetch import WebFetchTool, WebSearchTool


class Project:
    """A LoopReview project: one Coder, one Reviewer, one loop session."""

    def __init__(self, project_id: str, name: str, description: str,
                 workspace: Path, work_dir: str = "",
                 db: Optional["Persistence"] = None):
        self.id = project_id
        self.name = name
        self.description = description
        self.workspace = workspace
        self.work_dir = work_dir or str(workspace)
        self.created_at = time.time()
        self.status = "active"
        self.requirements: str = ""
        self.coder: Optional[KairosAgent] = None
        self.reviewer: Optional[KairosAgent] = None
        self.loop_session: Optional[Any] = None
        self.loop_task: Optional[asyncio.Task] = None
        self._db: Optional["Persistence"] = db

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "workspace": str(self.workspace),
            "work_dir": self.work_dir,
            "created_at": self.created_at,
            "status": self.status,
            "requirements": self.requirements,
            "coder_id": self.coder.agent_id if self.coder else None,
            "reviewer_id": self.reviewer.agent_id if self.reviewer else None,
            "files": self._db.list_files(self.id) if self._db else [],
        }


_VALID_SPECIALISTS = {
    "security_reviewer", "perf_reviewer",
    "design_reviewer", "test_reviewer",
    "docs_reviewer", "refactor_reviewer",
}

REVIEW_FOCUS_MAP = {
    "security_reviewer": "security",
    "perf_reviewer": "performance",
    "design_reviewer": "design",
    "test_reviewer": "test",
    "docs_reviewer": "documentation",
    "refactor_reviewer": "code_quality",
}


def _load_loop_config() -> dict:
    """Read loop settings from data/settings.json -> loop_config.

    Returns defaults when the file is missing or malformed. Silently
    drops unknown specialist names so a typo in settings.json doesn't
    crash the loop.
    """
    defaults = {"specialists": [], "best_of_n": 1, "review_focus": []}
    try:
        from kairos.config.settings import settings
        path = settings.data_dir / "settings.json"
        if not path.exists():
            return defaults
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        loop_cfg = cfg.get("loop_config", {}) or {}

        specialists = list(loop_cfg.get("specialists", []) or [])
        unknown = [s for s in specialists if s not in _VALID_SPECIALISTS]
        if unknown:
            logger.warning(
                "ignoring unknown specialist role(s) %s; valid: %s",
                unknown, sorted(_VALID_SPECIALISTS),
            )
            specialists = [s for s in specialists if s in _VALID_SPECIALISTS]

        try:
            best_of_n = int(loop_cfg.get("best_of_n", 1) or 1)
        except (TypeError, ValueError):
            logger.warning("loop_config.best_of_n is not an int; using 1")
            best_of_n = 1
        best_of_n = max(1, min(best_of_n, 5))

        review_focus = list(loop_cfg.get("review_focus", []) or [])
        if not review_focus:
            review_focus = [REVIEW_FOCUS_MAP[s] for s in specialists if s in REVIEW_FOCUS_MAP]

        return {"specialists": specialists, "best_of_n": best_of_n,
                "review_focus": review_focus}
    except json.JSONDecodeError as e:
        logger.warning("data/settings.json is not valid JSON: %s; using defaults", e)
        return defaults
    except Exception:
        logger.exception("failed to load loop config; using defaults")
        return defaults


_AUTO_ROUTE_KEYWORDS = {
    "docs_reviewer": [
        "doc", "docs", "readme", "docstring", "comment",
        "documentation", "example", "tutorial",
        "文档", "注释", "示例", "教程", "说明",
    ],
    "refactor_reviewer": [
        "refactor", "cleanup", "duplication", "duplicated", "dead code",
        "complexity", "naming", "code quality",
        "重构", "重复", "清理", "代码质量",
    ],
    "security_reviewer": [
        "auth", "login", "oauth", "jwt", "password", "credential",
        "permission", "xss", "csrf", "injection", "encrypt", "token",
        "身份", "登录", "权限", "密码", "加密", "安全", "注入",
    ],
    "perf_reviewer": [
        "performance", "perf", "latency", "throughput", "benchmark",
        "slow", "optimize", "optimise", "cache", "p99", "p95",
        "性能", "优化", "缓存", "快", "慢", "吞吐量", "延迟",
    ],
    "design_reviewer": [
        "ui", "ux", "design", "css", "responsive", "animation",
        "layout", "theme", "color", "typography",
        "界面", "互动", "样式", "布局", "主题", "动画",
    ],
    "test_reviewer": [
        "test", "spec", "pytest", "unittest", "coverage", "tdd",
        "测试", "覆盖率", "单元测试",
    ],
}


def _auto_route_specialists(requirement: str) -> list:
    """Inspect the user's requirement and return the set of focus areas
    that look obviously relevant based on simple keyword matching.

    Returns a list of canonical focus names (subset of REVIEW_FOCUS_MAP
    values). Caller should union with the user-configured list.
    """
    if not requirement:
        return []
    text = requirement.lower()
    matched = []
    for role, keywords in _AUTO_ROUTE_KEYWORDS.items():
        if role not in _VALID_SPECIALISTS:
            continue
        if any(kw in text for kw in keywords):
            focus = REVIEW_FOCUS_MAP.get(role)
            if focus and focus not in matched:
                matched.append(focus)
    return matched

class Orchestrator:
    """Bootstraps projects and runs Coder <-> Reviewer loops."""

    _SPECIALIST_CLASSES = {
        "security_reviewer": None,
        "perf_reviewer": None,
        "design_reviewer": None,
        "test_reviewer": None,
        "docs_reviewer": None,
        "refactor_reviewer": None,
    }

    def __init__(self, model_router: ModelRouter,
                 workspace_base: Path = Path("./workspace")):
        self.model_router = model_router
        self.workspace_base = workspace_base
        self.message_bus = MessageBus()
        self._projects: Dict[str, Project] = {}
        self._agents: Dict[str, KairosAgent] = {}

        from kairos.config.settings import settings
        db_path = settings.data_dir / "kairos.db"
        self._db = Persistence(db_path)
        self._load_projects()
        self.message_bus.add_listener(self._persist_message)

    def _persist_message(self, msg):
        try:
            self._db.save_message(msg)
        except Exception:
            logger.debug("Failed to persist message: %s", msg.topic, exc_info=True)

    def _load_projects(self):
        for row in self._db.load_projects():
            workspace = Path(row["workspace"])
            if not workspace.exists():
                workspace.mkdir(parents=True, exist_ok=True)
            project = Project(
                project_id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                workspace=workspace,
                work_dir=row["work_dir"] or "",
                db=self._db,
            )
            status = row["status"] or "active"
            if status == "running":
                status = "active"
            project.status = status
            project.requirements = row["requirements"] or ""
            project.created_at = row["created_at"] or 0
            self._projects[project.id] = project
            self._create_agents(project)

    def create_project(self, name: str, description: str, work_dir: str = "") -> Project:
        project_id = uuid.uuid4().hex[:8]
        workspace = self.workspace_base / project_id
        workspace.mkdir(parents=True, exist_ok=True)
        project = Project(project_id, name, description, workspace, work_dir, db=self._db)
        self._projects[project_id] = project
        self._db.save_project(project)
        self._create_agents(project)
        return project

    def _create_agents(self, project: Project):
        effective_root = project.work_dir or str(project.workspace)
        Path(effective_root).mkdir(parents=True, exist_ok=True)

        coder_tools = [
            FileReadTool(allowed_root=effective_root),
            FileEditTool(allowed_root=effective_root),
            FileEditReplaceTool(allowed_root=effective_root),
            MultiEditTool(allowed_root=effective_root),
            GrepTool(allowed_root=effective_root),
            FindTool(allowed_root=effective_root),
            GitTool(allowed_root=effective_root),
            TerminalTool(allowed_cwd=effective_root),
            WebFetchTool(),
            WebSearchTool(),
        ]
        subagent_tool = SubagentTool(allowed_root=effective_root)
        coder_tools.append(subagent_tool)

        reviewer_tools = [
            FileReadTool(allowed_root=effective_root),
            GrepTool(allowed_root=effective_root),
            FindTool(allowed_root=effective_root),
            GitTool(allowed_root=effective_root),
            TerminalTool(allowed_cwd=effective_root),
        ]

        yaml_prompts = self._load_yaml_prompts()

        coder_provider = self.model_router.get_provider_for_role("coder")
        reviewer_provider = self.model_router.get_provider_for_role("reviewer")

        project.coder = self._make_agent(
            project.id, "coder", Coder, coder_provider, coder_tools,
            self.message_bus, yaml_prompts,
        )
        project.reviewer = self._make_agent(
            project.id, "reviewer", Reviewer, reviewer_provider, reviewer_tools,
            self.message_bus, yaml_prompts,
        )

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

    def get_project(self, project_id: str) -> Optional[Project]:
        return self._projects.get(project_id)

    def list_projects(self) -> List[Project]:
        return list(self._projects.values())

    def delete_project(self, project_id: str):
        project = self._projects.pop(project_id, None)
        if not project:
            return
        if project.loop_task and not project.loop_task.done():
            project.loop_task.cancel()
        self._db.delete_project_memory(project_id)
        for agent_id in [project.coder.agent_id if project.coder else None,
                          project.reviewer.agent_id if project.reviewer else None]:
            if agent_id:
                self._agents.pop(agent_id, None)

    def save_requirements(self, project_id: str, requirements: str):
        project = self._projects.get(project_id)
        if not project:
            return
        project.requirements = requirements
        self._db.save_project(project)

    def add_reference_file(self, project_id: str, name: str, mime: str,
                            content: bytes) -> str:
        return self._db.add_file(project_id, name, mime, content)

    def list_reference_files(self, project_id: str) -> list:
        return self._db.list_files(project_id)

    def get_reference_file(self, file_id: str) -> Optional[dict]:
        return self._db.get_file(file_id)

    def delete_reference_file(self, file_id: str) -> bool:
        return self._db.delete_file(file_id)

    def build_reference_digest(self, project_id: str) -> str:
        files = self._db.list_files(project_id)
        if not files:
            return ""
        chunks = ["## Reference files (user-uploaded context)"]
        for meta in files[:8]:
            payload = self._db.get_file(meta["id"])
            if not payload:
                continue
            body = payload.get("content", b"").decode("utf-8", errors="replace")
            name = meta["name"]
            chunks.append(f"### {name}\n```\n{body[:3000]}\n```")
        return "\n\n".join(chunks)

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

    async def start_loop(self, project_id: str, requirement: str) -> str:
        """Start the Coder <-> Reviewer loop. Returns the loop session id.

        The loop runs in the background as a single asyncio.Task. The HTTP
        handler returns immediately. The UI watches the loop via WS events.

        If the project has reference files uploaded, a digest of them is
        prepended to the requirement so the Coder can use them as context
        in its first round. Subsequent rounds don't re-inject.
        """
        from kairos.loop.review_loop import LoopSession, run_loop

        project = self._projects.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        if project.loop_task and not project.loop_task.done():
            raise ValueError(f"Loop already running for project {project_id}")

        project.requirements = requirement
        project.status = "running"
        self._db.save_project(project)

        ref_digest = self.build_reference_digest(project_id)
        pref_block = self.build_preferences_block(project_id)
        if ref_digest:
            requirement = f"{ref_digest}\n\n## User Requirement\n{requirement}"
        if pref_block:
            requirement = pref_block + "\n\n" + requirement

        loop_cfg = _load_loop_config()
        review_focus = list(loop_cfg.get("review_focus") or [])
        try:
            focus_needed = [s for s in _auto_route_specialists(requirement)
                            if s not in review_focus]
            if focus_needed:
                review_focus = sorted(set(review_focus) | set(focus_needed))
        except Exception:
            logger.debug("review-focus auto-route failed (non-fatal)", exc_info=True)

        specialist_reviewers = self._instantiate_specialists(
            project_id, list(loop_cfg.get("specialists") or [])
        )

        session = LoopSession(
            project=project,
            message_bus=self.message_bus,
            coder=project.coder,
            reviewer=project.reviewer,
            persistence=self._db,
            best_of_n=int(loop_cfg.get("best_of_n", 1) or 1),
            review_focus=review_focus,
            specialist_reviewers=specialist_reviewers,
        )
        project.loop_session = session
        project.loop_task = asyncio.create_task(
            run_loop(session, requirement),
            name=f"loop-{project_id}",
        )
        project.loop_task.add_done_callback(
            lambda t: self._on_loop_done(project_id, t)
        )
        return session.session_id

    def stop_loop(self, project_id: str) -> bool:
        project = self._projects.get(project_id)
        if not project or not project.loop_session:
            return False
        project.loop_session.request_stop()
        project.loop_session.reject_plan()
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

    def refresh_all_agents(self):
        for agent_id, agent in self._agents.items():
            role = agent_id.split(".")[-1] if "." in agent_id else agent.role
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


}
