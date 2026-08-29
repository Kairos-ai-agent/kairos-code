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
import dataclasses
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


@dataclasses.dataclass(eq=False)
class ProjectRuntime:
    """Per-project live resources attached by the orchestrator.

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
        # Per-project best-of-N setting (read by the API endpoints and
        # passed to LoopSession at loop start). 1 = single attempt.
        self.best_of_n: int = 1
        # Free-form project metadata. The Coder sub-mode (read_only /
        # sandbox / default) is read from here at agent construction
        # time. Other extensions (custom prompts, override models) are
        # encouraged to use the same dict.
        self.metadata: dict = {}
        # Per-project live resources wired in by the orchestrator.
        # Each subsystem (MCP, worktree, guardrail, skills watcher,
        # manifest) attaches itself here on _create_agents so we
        # can clean up on shutdown without hunting for state.
        self.runtime: "ProjectRuntime" = ProjectRuntime()

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

# Minimum requirement length before auto-routing kicks in. Short
# prompts like "fix typo in readme" almost never benefit from a
# specialist — the cost of dispatching them outweighs the benefit,
# and they tend to false-positive on incidental keywords ("readme"
# alone is too weak a signal to enable the docs reviewer).
_AUTO_ROUTE_MIN_LENGTH = 25


def _auto_route_specialists(requirement: str) -> List[str]:
    """Pick which specialist Reviewers should be added to a loop.

    Returns a list of BOTH the specialist role name AND its
    mapped review-focus value for every keyword hit. The role name
    is what the orchestrator uses to instantiate specialist agents;
    the focus value is what gets stored in `review_focus` so the
    single Reviewer knows which lenses to apply.

    Short requirements (length < _AUTO_ROUTE_MIN_LENGTH) return []
    so we do not over-dispatch a specialist on a one-liner.
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

class Orchestrator:
    """Bootstraps projects and runs Coder <-> Reviewer loops."""

    # In-memory cache of all specialist classes we have tried to import.
    # The cache avoids hitting importlib on every loop start; it also
    # memoizes the (occasionally expensive) class lookup. None means
    # "not yet tried"; False means "tried and failed".
    _SPECIALIST_CLASSES: Dict[str, Any] = {}

    def __init__(self, model_router: ModelRouter,
                 workspace_base: Path = Path("./workspace"),
                 db: Optional["Persistence"] = None):
        self.model_router = model_router
        self.workspace_base = workspace_base
        self.message_bus = MessageBus()
        self._projects: Dict[str, Project] = {}
        self._agents: Dict[str, KairosAgent] = {}
        self._dispatch_tasks: set = set()
        # In-memory cache of per-project Best-of-N override; the API
        # reads/writes this and start_loop copies it into the session.
        if db is None:
            from kairos.config.settings import settings
            db_path = settings.data_dir / "kairos.db"
            self._db = Persistence(db_path)
        else:
            self._db = db
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

        # Optionally isolate each role in its own git worktree.
        # Only when the project root is itself a git repo. Failure
        # is logged + recorded on the runtime, not raised — a
        # non-git or unwritable project still gets a working agent.
        coder_root = effective_root
        reviewer_root = effective_root
        try:
            cw, rw = self._create_role_worktrees(effective_root)
            if cw is not None:
                project.runtime.coder_worktree = cw
                coder_root = str(cw.path)
            if rw is not None:
                project.runtime.reviewer_worktree = rw
                reviewer_root = str(rw.path)
        except Exception as e:  # noqa: BLE001
            logger.warning("worktree setup failed for %s: %s", project.id, e)
            project.runtime.attach_errors.append(f"worktree: {e}")

        coder_tools = [
            FileReadTool(allowed_root=coder_root),
            FileEditTool(allowed_root=coder_root),
            FileEditReplaceTool(allowed_root=coder_root),
            MultiEditTool(allowed_root=coder_root),
            GrepTool(allowed_root=coder_root),
            FindTool(allowed_root=coder_root),
            GitTool(allowed_root=coder_root),
            TerminalTool(allowed_cwd=coder_root),
            WebFetchTool(),
            WebSearchTool(),
        ]

        # R38.6 §30: auto-checkpoint the project's files before any
        # write. The checkpointer is shared by the 3 file tools so
        # the user can restore from the Workbench panel even if the
        # agent's session crashed mid-loop. Best-effort: a failed
        # snapshot is logged but never blocks the write.
        try:
            from kairos.auto_checkpoint import AutoCheckpointer
            checkpointer = AutoCheckpointer(project_dir=project.work_dir
                                            or project.workspace)
            for tool in coder_tools:
                if isinstance(tool, (FileEditTool, FileEditReplaceTool,
                                       MultiEditTool)):
                    tool._checkpointer = checkpointer
        except Exception as exc:  # noqa: BLE001
            logger.debug("auto-checkpointer setup failed: %s", exc)

        subagent_tool = SubagentTool(allowed_root=coder_root)
        coder_tools.append(subagent_tool)

        # Apply Coder sub-mode (default / read_only / sandbox) to the
        # tool list. Sandbox mode keeps the tool but records the intent;
        # a real path-redirecting wrapper would replace mutating tools
        # with worktree-scoped variants. read_only drops them entirely.
        try:
            from kairos.coder_modes import (
                CoderMode, apply_mode, mode_from_project_metadata,
            )
            coder_mode = mode_from_project_metadata(
                getattr(project, "metadata", None)
            )
            coder_tools, coder_policy = apply_mode(
                coder_tools, coder_mode,
            )
            project.runtime.coder_mode = coder_mode.value
            project.runtime.coder_policy = coder_policy.to_dict()
        except Exception as e:  # noqa: BLE001
            logger.warning("coder mode policy failed for %s: %s", project.id, e)
            project.runtime.attach_errors.append(f"coder_mode: {e}")

        reviewer_tools = [
            FileReadTool(allowed_root=reviewer_root),
            GrepTool(allowed_root=reviewer_root),
            FindTool(allowed_root=reviewer_root),
            GitTool(allowed_root=reviewer_root),
            TerminalTool(allowed_cwd=reviewer_root),
        ]

        # Load MCP tools (best-effort). Tools are appended to both
        # roles' toolset so the agent can call them; the underlying
        # subprocess registry is owned by the runtime so we can
        # close it on shutdown.
        try:
            mcp_tools = self._attach_mcp(project, coder_root)
            coder_tools.extend(mcp_tools)
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP attach failed for %s: %s", project.id, e)
            project.runtime.attach_errors.append(f"mcp: {e}")

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

        # Output guardrail on the Coder: the project Reviewer double-
        # checks the Coder's final text and records a GuardrailResult
        # on the AgentTask. We use post-construction assignment
        # because the guardrail needs the Reviewer (created just
        # above) and the KairosAgent constructor expects it up-front.
        try:
            from kairos.guardrails import OutputGuardrail
            project.runtime.output_guardrail = OutputGuardrail(
                reviewer=project.reviewer,
                blocking=False,
                message_bus=self.message_bus,
            )
            project.coder._output_guardrail = project.runtime.output_guardrail
        except Exception as e:  # noqa: BLE001
            logger.warning("OutputGuardrail attach failed for %s: %s",
                           project.id, e)
            project.runtime.attach_errors.append(f"guardrail: {e}")

        # Live-reload of skills for this project. The watcher polls
        # once a second; if no project_dir is set we skip it.
        try:
            self._attach_skills_watcher(project, effective_root)
        except Exception as e:  # noqa: BLE001
            logger.warning("SkillsWatcher attach failed for %s: %s",
                           project.id, e)
            project.runtime.attach_errors.append(f"watcher: {e}")

        # Manifest: load + apply (model-router override currently a
        # no-op — we record the loaded manifest on the runtime for
        # the API layer to surface).
        try:
            self._attach_manifest(project, effective_root)
        except Exception as e:  # noqa: BLE001
            logger.warning("Manifest load failed for %s: %s", project.id, e)
            project.runtime.attach_errors.append(f"manifest: {e}")

        project.runtime.attached_at = time.time()

    # ---- integration helpers (P2 + Round-3 wiring) ----
    #
    # Each helper is best-effort: a failure (MCP server not running,
    # no git in the project, no skills dir) is recorded in
    # `project.runtime.attach_errors` and the rest of the project
    # continues. None of the helpers raise unless the caller wraps
    # them in try/except (which _create_agents does).

    def _create_role_worktrees(self, work_dir: str):
        """Create per-role worktrees if `work_dir` is inside a git repo.

        Returns (coder_wt, reviewer_wt). Each is a Worktree or None.
        Raises on hard failure (git missing, malformed repo) — caller
        logs and continues.
        """
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

    def _attach_mcp(self, project: Project, work_dir: str) -> List[Any]:
        """Load MCP servers from `<work_dir>/.kairos/mcp.yaml` and
        start them. Returns the list of MCP-sourced tools (Kairos
        BaseTool instances) ready to append to a role's toolset.

        The registry is stored on `project.runtime.mcp_registry` so
        it can be closed on shutdown.
        """
        from kairos.mcp_client import McpRegistry
        if not work_dir:
            return []
        reg = McpRegistry()
        try:
            reg.load(project_dir=Path(work_dir))
        except Exception as e:  # noqa: BLE001
            logger.debug("MCP load skipped for %s: %s", project.id, e)
            return []
        # start_all is async; for the synchronous _create_agents
        # path we run it via asyncio.run if there's a loop, else
        # we skip (MCP servers will be started on first async tick).
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Fire-and-forget: schedule start_all on the running loop.
                loop.create_task(reg.start_all())
            else:
                loop.run_until_complete(reg.start_all())
        except RuntimeError:
            # No event loop — start synchronously (start_all is
            # an async coroutine; we use asyncio.run).
            try:
                asyncio.run(reg.start_all())
            except Exception as e:  # noqa: BLE001
                logger.debug("MCP start_all failed for %s: %s", project.id, e)
                return []
        project.runtime.mcp_registry = reg
        return list(reg.all_tools())

    def _attach_manifest(self, project: Project, work_dir: str) -> None:
        """Load the project's manifest and apply its settings.

        Currently this just records the manifest on the runtime; the
        per-role provider override and trust path list are surfaced
        through the API but don't yet mutate the live providers. The
        ground is laid here so callers can read `project.runtime.
        manifest` without re-reading the YAML.
        """
        from kairos.manifest import load as load_manifest
        if not work_dir:
            return
        manifest = load_manifest(project_dir=Path(work_dir))
        project.runtime.manifest = manifest

    def _attach_skills_watcher(self, project: Project, work_dir: str) -> None:
        """Start a SkillsWatcher on `<work_dir>/.kairos/skills/`.

        The watcher polls once a second for added / modified /
        removed skill files. On change it calls a callback that
        currently just logs — wiring the callback into a live
        SkillsLoader is left to the API/UI layer (which can call
        `kairos.skills.SkillsLoader.refresh()`).
        """
        from kairos.skills_watcher import SkillsWatcher
        if not work_dir:
            return
        skills_dir = Path(work_dir) / ".kairos" / "skills"
        if not skills_dir.exists():
            return
        watcher = SkillsWatcher(skill_dirs=[skills_dir], interval_s=1.0)
        # `start()` is async; we run it synchronously via asyncio.run
        # when no loop is running, or schedule it on the running loop.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(watcher.start())
            else:
                loop.run_until_complete(watcher.start())
        except RuntimeError:
            asyncio.run(watcher.start())
        project.runtime.skills_watcher = watcher

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

    def get_project(self, project_id: str) -> Optional[Project]:
        return self._projects.get(project_id)

    def list_projects(self) -> List[Project]:
        return list(self._projects.values())

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

    def _close_project_runtime(self, project: Project) -> None:
        """Stop the watchers, close MCP subprocesses, remove worktrees.

        Best-effort: each subsystem is closed in its own try/except so
        one failure doesn't prevent the others from cleaning up.

        This is the **sync** path: it handles SkillsWatcher.stop()
        and WorktreeManager.cleanup() which are sync. The async
        MCP close_all is handled in `close()` (async) instead.
        """
        rt = project.runtime
        # 1) SkillsWatcher (sync stop)
        if rt.skills_watcher is not None:
            try:
                rt.skills_watcher.stop_sync()
            except Exception as e:  # noqa: BLE001
                logger.debug("skills_watcher stop failed: %s", e)
            rt.skills_watcher = None
        # 2) Worktrees
        for wt_attr in ("coder_worktree", "reviewer_worktree"):
            wt = getattr(rt, wt_attr, None)
            if wt is None:
                continue
            try:
                from kairos.worktree import WorktreeManager
                mgr = WorktreeManager(repo_path=Path(project.work_dir
                                                     or project.workspace))
                mgr.cleanup(wt, remove_branch=True)
            except Exception as e:  # noqa: BLE001
                logger.debug("worktree cleanup failed for %s: %s", wt_attr, e)
            setattr(rt, wt_attr, None)

    async def _close_project_runtime_async(self, project: Project) -> None:
        """Async counterpart: closes the MCP registry's subprocesses.

        The sync path (`_close_project_runtime`) handles watchers and
        worktrees; this adds the MCP close_all which is async.
        """
        rt = project.runtime
        if rt.mcp_registry is not None:
            try:
                await rt.mcp_registry.close_all()
            except Exception as e:  # noqa: BLE001
                logger.debug("MCP close_all failed: %s", e)
            rt.mcp_registry = None

    async def close(self) -> None:
        """Tear down the orchestrator: stop every project's runtime.

        Called from FastAPI's lifespan shutdown. Idempotent — calling
        twice is a no-op.
        """
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

    def save_requirements(self, project_id: str, requirements: str):
        project = self._projects.get(project_id)
        if not project:
            return
        project.requirements = requirements
        self._db.save_project(project)

    def add_reference_file(self, project_id: str, name: str, mime: str,
                            content) -> str:
        """Persist a reference file. `content` may be bytes (from the
        HTTP upload endpoint) or a str (already-decoded text). We
        coerce to UTF-8 text so the schema column stays TEXT, and
        generate the row id + size here so the caller can return
        the file_id without knowing the SQLite schema.
        """
        if isinstance(content, (bytes, bytearray)):
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                import base64 as _b64
                text = _b64.b64encode(bytes(content)).decode("ascii")
        else:
            text = str(content or "")
        file_id = uuid.uuid4().hex
        size = len(text)
        self._db.add_file(file_id, project_id, name, mime or "application/octet-stream", size, text)
        return file_id

    def list_reference_files(self, project_id: str) -> list:
        return self._db.list_files(project_id)

    def get_reference_file(self, file_id: str) -> Optional[dict]:
        return self._db.get_file(file_id)

    def delete_reference_file(self, file_id: str) -> bool:
        return self._db.delete_file(file_id)

    def build_reference_digest(self, project_id: str) -> str:
        """Build a prompt-friendly digest of all uploaded reference
        files. Small files are inlined verbatim so the Coder can
        read them; large files are truncated to a preview with a
        `… truncated` marker. Returns "" when no files exist so
        the caller can splice it in without a sentinel check.
        """
        files = self._db.list_files(project_id)
        if not files:
            return ""
        chunks = ["## 参考资料"]
        preview_bytes = 3000
        for meta in files[:8]:
            payload = self._db.get_file(meta["id"])
            if not payload:
                continue
            raw = payload.get("content", "")
            if isinstance(raw, (bytes, bytearray)):
                body = raw.decode("utf-8", errors="replace")
            else:
                body = str(raw or "")
            name = meta.get("name", "?")
            size = int(meta.get("size") or len(body))
            if size <= preview_bytes:
                chunks.append("### " + name + "\n```\n" + body + "\n```")
            else:
                head = body[:preview_bytes]
                chunks.append(
                    "### " + name
                    + "\n```\n" + head
                    + "\n\n… truncated (showing first " + str(preview_bytes)
                    + " of " + str(size) + " bytes)\n```"
                )
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

    async def start_loop(self, project_id: str, requirement: str) -> str:
        """Start the Coder <-> Reviewer loop. Returns the loop session id.

        The loop runs in the background as a single asyncio.Task. The HTTP
        handler returns immediately. The UI watches the loop via WS events.

        Round 37 — new-project bootstrap:
          When the project has *no prior sessions*, the loop runs in
          ``unbounded=True`` mode (no LOOP_SAFETY_CAP) and the review
          focus is auto-set to ``["bug_reviewer"]`` (only check for
          actual bugs; do not nitpick style / architecture / security).
          The dev can still hit Stop from the UI. This is a UX win
          for the most common first-run case: a brand-new project
          where the cap is artificial and the reviewer nitpicking
          is more annoying than helpful.

          Existing projects keep the original behavior (cap + the
          review focus the user configured).

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
        mem_block = self.build_memory_block(project_id, requirement)
        if ref_digest:
            requirement = f"{ref_digest}\n\n## User Requirement\n{requirement}"
        if pref_block:
            requirement = pref_block + "\n\n" + requirement
        if mem_block:
            requirement = mem_block + "\n\n" + requirement

        loop_cfg = _load_loop_config()
        review_focus = list(loop_cfg.get("review_focus") or [])
        try:
            focus_needed = [s for s in _auto_route_specialists(requirement)
                            if s not in review_focus]
            if focus_needed:
                review_focus = sorted(set(review_focus) | set(focus_needed))
        except Exception:
            logger.debug("review-focus auto-route failed (non-fatal)", exc_info=True)

        # Round 37: detect a brand-new project (no prior sessions).
        # On first run we lift the LOOP_SAFETY_CAP and narrow the
        # reviewer to bug detection only. The dev can still hit Stop.
        is_new_project = self._is_new_project(project_id)
        unbounded = is_new_project
        if is_new_project and not review_focus:
            # Only override when the user hasn't already configured
            # a custom focus. The "bug_reviewer" focus scopes the
            # reviewer to "look for actual bugs" and explicitly tells
            # it NOT to comment on style / security / architecture.
            review_focus = ["bug_reviewer"]

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
            run_loop(session, requirement, unbounded=unbounded),
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



