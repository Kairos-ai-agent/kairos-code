"""ProjectFactory - Creates and configures projects with agents.

Handles:
- Project creation and loading from persistence
- Agent construction and tool binding
- Subsystem attachment (MCP, worktrees, skills, guardrails, manifest)
- Project lifecycle management

This module is responsible for the "bootstrap" phase of a project,
while LoopController handles the runtime loop management.
"""
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

from kairos.core.message_bus import MessageBus
from kairos.core.persistence import Persistence
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

logger = logging.getLogger(__name__)


# ============================================================================
# Data Classes
# ============================================================================

@dataclasses.dataclass(eq=False)
class ProjectRuntime:
    """Per-project live resources attached by the factory.

    Holds the McpRegistry, worktree paths, skills watcher, manifest
    and output guardrail that are wired up when a project is created
    (or loaded from persistence). All fields are optional; a project
    may have any subset depending on what the runtime could attach
    (e.g. a non-git project has no worktrees).
    """
    manifest: Optional[Any] = None
    mcp_registry: Optional[Any] = None
    coder_worktree: Optional[Any] = None  # kairos.worktree.Worktree
    reviewer_worktree: Optional[Any] = None
    skills_watcher: Optional[Any] = None
    output_guardrail: Optional[Any] = None
    attached_at: float = 0.0
    attach_errors: List[str] = dataclasses.field(default_factory=list)
    # Coder sub-mode applied to this project (default / read_only / sandbox).
    coder_mode: str = "default"
    # Last computed ToolPolicy: which tools survived, which were blocked.
    coder_policy: Optional[dict] = None


@dataclasses.dataclass
class Project:
    """A LoopReview project: one Coder, one Reviewer, one loop session."""

    id: str
    name: str
    description: str
    workspace: Path
    work_dir: str = ""
    db: Optional[Persistence] = None
    
    # Runtime state
    created_at: float = 0.0
    status: str = "active"
    requirements: str = ""
    
    # Agent instances
    coder: Optional[Any] = None
    reviewer: Optional[Any] = None
    loop_session: Optional[Any] = None
    loop_task: Optional[asyncio.Task] = None
    
    # Configuration
    best_of_n: int = 1
    metadata: dict = dataclasses.field(default_factory=dict)
    runtime: ProjectRuntime = dataclasses.field(default_factory=ProjectRuntime)

    @property
    def _db(self) -> Optional[Persistence]:
        return self.db

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


# ============================================================================
# Constants
# ============================================================================

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

_AUTO_ROUTE_MIN_LENGTH = 25


# ============================================================================
# Helper Functions
# ============================================================================

def _load_loop_config() -> dict:
    """Read loop settings from data/settings.json -> loop_config.

    Returns defaults when the file is missing or malformed. Silently
    drops unknown specialist names so a typo in settings.json doesn't
    crash the loop.
    """
    defaults = {
        "specialists": [],
        "best_of_n": 1,
        "review_focus": [],
        "require_test_evidence": False,
        "difficulty_routing": False,
    }
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

        return {
            "specialists": specialists,
            "best_of_n": best_of_n,
            "review_focus": review_focus,
            "require_test_evidence": bool(loop_cfg.get("require_test_evidence", False)),
            "difficulty_routing": bool(loop_cfg.get("difficulty_routing", False)),
        }
    except json.JSONDecodeError as e:
        logger.warning("data/settings.json is not valid JSON: %s; using defaults", e)
        return defaults
    except Exception:
        logger.exception("failed to load loop config; using defaults")
        return defaults


def _auto_route_specialists(requirement: str) -> List[str]:
    """Pick which specialist Reviewers should be added to a loop.

    Returns a list of BOTH the specialist role name AND its
    mapped review-focus value for every keyword hit.
    """
    if not requirement or len(requirement) < _AUTO_ROUTE_MIN_LENGTH:
        return []
    text = requirement.lower()
    matched: List[str] = []
    for role, keywords in _AUTO_ROUTE_KEYWORDS.items():
        if any(k in text for k in keywords):
            matched.append(role)
            focus = REVIEW_FOCUS_MAP.get(role)
            if focus and focus not in matched:
                matched.append(focus)
    return matched


def _load_yaml_prompts(workspace_base: Path) -> Dict[str, str]:
    """Load system prompts from agents_config.yaml."""
    yaml_path = workspace_base.parent / "config" / "agents_config.yaml"
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


# ============================================================================
# ProjectFactory Class
# ============================================================================

class ProjectFactory:
    """Creates and configures projects with their agents and subsystems.
    
    Responsibilities:
    - Create new projects with isolated workspaces
    - Load existing projects from persistence
    - Construct Coder and Reviewer agents with appropriate tools
    - Attach subsystems (MCP, worktrees, skills, guardrails, manifest)
    - Manage specialist reviewers
    """

    # In-memory cache of all specialist classes we have tried to import.
    _SPECIALIST_CLASSES: Dict[str, Any] = {}

    def __init__(self, model_router: ModelRouter,
                 workspace_base: Path = Path("./workspace"),
                 db: Optional[Persistence] = None):
        self.model_router = model_router
        self.workspace_base = workspace_base
        self._db = db or Persistence(workspace_base.parent / "data" / "kairos.db")

    def create_project(self, name: str, description: str, 
                       work_dir: str = "") -> Project:
        """Create a new project with isolated workspace and agents."""
        project_id = uuid.uuid4().hex[:8]
        workspace = self.workspace_base / project_id
        workspace.mkdir(parents=True, exist_ok=True)
        project = Project(
            project_id=project_id,
            name=name,
            description=description,
            workspace=workspace,
            work_dir=work_dir or str(workspace),
            db=self._db,
        )
        self._attach_agents(project)
        self._db.save_project(project)
        return project

    def load_projects(self) -> List[Project]:
        """Load all non-archived projects from persistence."""
        projects = []
        for row in self._db.load_projects():
            try:
                project = self._hydrate_from_row(row)
                projects.append(project)
            except Exception as e:
                logger.warning("Failed to load project %s: %s", row.get("id"), e)
        return projects

    def get_or_create_project(self, project_id: str) -> Optional[Project]:
        """Get existing project or create a stub for self-healing."""
        rows = self._db.load_projects(include_archived=True)
        for row in rows:
            if row.get("id") == project_id:
                return self._hydrate_from_row(row)
        return None

    def _hydrate_from_row(self, row: dict) -> Project:
        """Create a Project from a database row."""
        workspace = Path(row["workspace"])
        if not workspace.is_absolute():
            from kairos.config.settings import settings
            workspace = (settings.workspace_dir.parent / workspace).resolve()
        if not workspace.exists():
            workspace.mkdir(parents=True, exist_ok=True)
        
        project = Project(
            project_id=row["id"],
            name=row["name"],
            description=row.get("description", ""),
            workspace=workspace,
            work_dir=row.get("work_dir", ""),
            db=self._db,
            created_at=row.get("created_at", 0) or time.time(),
            status=row.get("status", "active") or "active",
            requirements=row.get("requirements", ""),
        )
        self._attach_agents(project)
        return project

    def _attach_agents(self, project: Project) -> None:
        """Attach Coder and Reviewer agents to a project.
        
        This is the core initialization that wires up all tools,
        subsystems, and configuration for both agents.
        """
        effective_root = project.work_dir or str(project.workspace)
        Path(effective_root).mkdir(parents=True, exist_ok=True)

        # Create per-role worktrees if possible
        coder_root, reviewer_root = self._create_role_worktrees(effective_root)
        if coder_root:
            project.runtime.coder_worktree = coder_root
        if reviewer_root:
            project.runtime.reviewer_worktree = reviewer_root

        # Build tool lists
        coder_tools = self._build_coder_tools(coder_root or effective_root, project)
        reviewer_tools = self._build_reviewer_tools(reviewer_root or effective_root)

        # Wire agents
        yaml_prompts = _load_yaml_prompts(self.workspace_base)
        self._wire_agents(project, coder_tools, reviewer_tools, yaml_prompts)

        # Attach subsystems
        self._attach_subsystems(project, effective_root)

        project.runtime.attached_at = time.time()

    def _build_coder_tools(self, root: str, project: Project) -> List[Any]:
        """Build the full tool list for the Coder agent."""
        tools = [
            FileReadTool(allowed_root=root),
            FileEditTool(allowed_root=root),
            FileEditReplaceTool(allowed_root=root),
            MultiEditTool(allowed_root=root),
            GrepTool(allowed_root=root),
            FindTool(allowed_root=root),
            GitTool(allowed_root=root),
            TerminalTool(allowed_cwd=root),
            WebFetchTool(),
            WebSearchTool(),
        ]

        # Auto-checkpointer
        try:
            from kairos.auto_checkpoint import AutoCheckpointer
            checkpointer = AutoCheckpointer(project_dir=project.work_dir or project.workspace)
            for tool in tools:
                if isinstance(tool, (FileEditTool, FileEditReplaceTool, MultiEditTool)):
                    tool._checkpointer = checkpointer
        except Exception:
            logger.debug("auto-checkpointer setup failed")

        # Subagent tool
        subagent_tool = SubagentTool(allowed_root=root)
        tools.append(subagent_tool)

        # Apply coder mode
        try:
            from kairos.coder_modes import apply_mode, mode_from_project_metadata
            coder_mode = mode_from_project_metadata(getattr(project, "metadata", None))
            tools, policy = apply_mode(tools, coder_mode)
            project.runtime.coder_mode = coder_mode.value
            project.runtime.coder_policy = policy.to_dict()
        except Exception as e:
            logger.warning("coder mode policy failed: %s", e)
            project.runtime.attach_errors.append(f"coder_mode: {e}")

        # MCP tools
        try:
            mcp_tools = self._attach_mcp(project, root)
            tools.extend(mcp_tools)
        except Exception as e:
            logger.warning("MCP attach failed: %s", e)
            project.runtime.attach_errors.append(f"mcp: {e}")

        return tools

    def _build_reviewer_tools(self, root: str) -> List[Any]:
        """Build the read-only tool list for the Reviewer agent."""
        return [
            FileReadTool(allowed_root=root),
            GrepTool(allowed_root=root),
            FindTool(allowed_root=root),
            GitTool(allowed_root=root),
            TerminalTool(allowed_cwd=root),
        ]

    def _wire_agents(self, project: Project, coder_tools: List[Any],
                     reviewer_tools: List[Any], yaml_prompts: Dict[str, str]) -> None:
        """Create and wire Coder and Reviewer agents."""
        from kairos.agents.roles import Coder, Reviewer

        def _wire(role: str, role_cls, tools, preferred):
            try:
                return self._make_agent(
                    project.id, role, role_cls, preferred, tools, yaml_prompts,
                )
            except Exception as exc:
                logger.warning("agent wiring failed for %s.%s: %s", project.id, role, exc)
                project.runtime.attach_errors.append(f"{role}: {exc}")
                try:
                    default_provider = self.model_router.get_provider_for_role("default")
                except Exception:
                    raise
                return self._make_agent(
                    project.id, role, role_cls, default_provider, tools, yaml_prompts,
                )

        coder_provider = self.model_router.get_provider_for_role("coder")
        reviewer_provider = self.model_router.get_provider_for_role("reviewer")

        project.coder = _wire("coder", Coder, coder_tools, coder_provider)
        project.reviewer = _wire("reviewer", Reviewer, reviewer_tools, reviewer_provider)

        # Output guardrail
        try:
            from kairos.guardrails import OutputGuardrail
            project.runtime.output_guardrail = OutputGuardrail(
                reviewer=project.reviewer,
                blocking=False,
                message_bus=MessageBus(),
            )
            project.coder._output_guardrail = project.runtime.output_guardrail
        except Exception as e:
            logger.warning("OutputGuardrail attach failed: %s", e)
            project.runtime.attach_errors.append(f"guardrail: {e}")

    def _make_agent(self, project_id: str, role: str, role_cls, provider,
                    tools: List[Any], yaml_prompts: Dict[str, str]) -> Any:
        """Create an agent instance with the given configuration."""
        agent_id = f"{project_id}.{role}"
        kwargs = {}
        if role in yaml_prompts:
            kwargs["system_prompt"] = yaml_prompts[role]
        
        agent = role_cls(
            agent_id=agent_id,
            llm_config=provider.config,
            message_bus=MessageBus(),
            tools=tools,
            **kwargs,
        )
        
        # Wire subagent parent reference
        for tool in tools:
            if tool.name == "spawn_subagent":
                tool.parent_agent = agent
                tool.project_id = project_id
        
        return agent

    def _attach_subsystems(self, project: Project, work_dir: str) -> None:
        """Attach all subsystems to a project."""
        # Skills watcher
        try:
            self._attach_skills_watcher(project, work_dir)
        except Exception as e:
            logger.warning("SkillsWatcher attach failed: %s", e)
            project.runtime.attach_errors.append(f"watcher: {e}")

        # Manifest
        try:
            self._attach_manifest(project, work_dir)
        except Exception as e:
            logger.warning("Manifest load failed: %s", e)
            project.runtime.attach_errors.append(f"manifest: {e}")

    def _attach_skills_watcher(self, project: Project, work_dir: str) -> None:
        """Start a SkillsWatcher for the project's skills directory."""
        from kairos.skills_watcher import SkillsWatcher
        if not work_dir:
            return
        skills_dir = Path(work_dir) / ".kairos" / "skills"
        if not skills_dir.exists():
            return
        watcher = SkillsWatcher(skill_dirs=[skills_dir], interval_s=1.0)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(watcher.start())
            else:
                asyncio.run(watcher.start())
        except RuntimeError:
            asyncio.run(watcher.start())
        project.runtime.skills_watcher = watcher

    def _attach_manifest(self, project: Project, work_dir: str) -> None:
        """Load and apply the project's manifest."""
        from kairos.manifest import load as load_manifest
        if not work_dir:
            return
        manifest = load_manifest(project_dir=Path(work_dir))
        project.runtime.manifest = manifest

    def _create_role_worktrees(self, work_dir: str):
        """Create per-role worktrees if work_dir is inside a git repo."""
        if os.environ.get("KAIROS_SKIP_WORKTREES", "").strip().lower() in ("1", "true", "yes", "on"):
            return (None, None)
        
        from kairos.worktree import WorktreeManager
        if not work_dir:
            return (None, None)
        
        try:
            mgr = WorktreeManager(repo_path=Path(work_dir))
        except Exception:
            return (None, None)
        
        try:
            coder_wt = mgr.create(branch_name=WorktreeManager.unique_branch_name("coder"))
            reviewer_wt = mgr.create(branch_name=WorktreeManager.unique_branch_name("reviewer"))
            return (str(coder_wt.path), str(reviewer_wt.path))
        except Exception as e:
            logger.warning("worktree setup failed: %s", e)
            return (None, None)

    def _attach_mcp(self, project: Project, work_dir: str) -> List[Any]:
        """Load and start MCP servers for the project."""
        from kairos.mcp_client import McpRegistry, should_defer_start
        if not work_dir:
            return []
        
        reg = McpRegistry()
        try:
            reg.load(project_dir=Path(work_dir))
        except Exception:
            return []
        
        # Same three cases as the orchestrator path; see the comment there.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(reg.start_all())
            elif should_defer_start():
                reg.defer_start()
            else:
                loop.run_until_complete(reg.start_all())
        except RuntimeError:
            if should_defer_start():
                reg.defer_start()
            else:
                try:
                    asyncio.run(reg.start_all())
                except Exception:
                    return []
        
        project.runtime.mcp_registry = reg
        return list(reg.all_tools())

    def get_specialist_classes(self, specialist_names: List[str]) -> List[Any]:
        """Create specialist reviewer agents for a project."""
        if not specialist_names:
            return []
        
        out = []
        for name in specialist_names:
            if name not in _VALID_SPECIALISTS:
                continue
            cls = self._resolve_specialist_class(name)
            if cls is None:
                continue
            try:
                agent = self._make_specialist_agent(name, cls)
                out.append(agent)
            except Exception:
                logger.exception("failed to create specialist %s", name)
        return out

    def _resolve_specialist_class(self, name: str):
        cached = self._SPECIALIST_CLASSES.get(name)
        if cached or cached is False:
            return cached or None
        try:
            from kairos.agents.roles import (
                SecurityReviewer, PerfReviewer, DesignReviewer,
                TestReviewer, DocsReviewer, RefactorReviewer,
            )
            specialists = {
                "security_reviewer": SecurityReviewer,
                "perf_reviewer": PerfReviewer,
                "design_reviewer": DesignReviewer,
                "test_reviewer": TestReviewer,
                "docs_reviewer": DocsReviewer,
                "refactor_reviewer": RefactorReviewer,
            }
            cls = specialists.get(name)
        except Exception:
            cls = None
        self._SPECIALIST_CLASSES[name] = cls or False
        return cls

    def _make_specialist_agent(self, name: str, cls: type) -> Any:
        """Create a specialist reviewer agent instance."""
        from kairos.agents.base import KairosAgent
        agent_id = f"specialist.{name}"
        provider = self.model_router.get_provider_for_role(name)
        return cls(
            agent_id=agent_id,
            llm_config=provider.config,
            message_bus=MessageBus(),
            tools=[],
        )

    def close_project_runtime(self, project: Project) -> None:
        """Clean up project runtime resources."""
        rt = project.runtime
        if rt.skills_watcher:
            try:
                rt.skills_watcher.stop_sync()
            except Exception:
                pass
            rt.skills_watcher = None
        for wt_attr in ("coder_worktree", "reviewer_worktree"):
            wt = getattr(rt, wt_attr, None)
            if wt is None:
                continue
            try:
                from kairos.worktree import WorktreeManager
                mgr = WorktreeManager(repo_path=Path(project.work_dir or project.workspace))
                mgr.cleanup(wt, remove_branch=True)
            except Exception:
                pass
            setattr(rt, wt_attr, None)
