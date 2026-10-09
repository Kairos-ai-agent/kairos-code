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
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# R38.6.4 packaging: lazy-import kairos.agents.* so PyInstaller
# can find the submodules (it misses top-level imports on packages
# whose __init__.py already pulls them in). The orchestrator is the
# first place that needs them after api.app; deferring the load to
# first-use means PyInstaller's --collect-submodules and the
# loader agree on the symbol resolution.
import importlib as _il


def _agent(name: str):
    return _il.import_module(name)


# R38.6.4 packaging: keep names available at module level for
# existing ``from kairos.core.orchestrator import Coder`` style
# imports — but resolve them lazily, on first attribute access.
# This is the same pattern as ``from kairos import kairos`` but
# using module __getattr__.
from kairos.core.message_bus import Message, MessageBus


def __getattr__(name):
    if name in {"AgentTask", "KairosAgent"}:
        mod = _il.import_module("kairos.agents.base")
        return getattr(mod, name)
    if name in {"Coder", "Reviewer"}:
        mod = _il.import_module("kairos.agents.roles")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter
from kairos.tools.base import ToolResult
from kairos.tools.code_search import CodeSearchTool
from kairos.tools.data_analyze import DataAnalyzeTool
from kairos.tools.doc_read import DocReadTool
from kairos.tools.file_edit import FileEditReplaceTool, FileEditTool, MultiEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.find import FindTool
from kairos.tools.git_tool import GitTool
from kairos.tools.grep_tool import GrepTool
from kairos.tools.history_search import HistorySearchTool
from kairos.tools.python_run import PythonRunTool
from kairos.tools.subagent import (SubagentResultTool, SubagentStatusTool,
                                   SubagentTool)
from kairos.tools.terminal import TerminalTool
from kairos.tools.todos import WriteTodosTool
from kairos.tools.webfetch import WebFetchTool, WebSearchTool
from kairos.tools.xlsx_read import XlsxReadTool


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

from kairos.core.orchestrator_parts.lifecycle import OrchLifecycleMixin
from kairos.core.orchestrator_parts.wiring import OrchWiringMixin
from kairos.core.orchestrator_parts.references import OrchReferenceMixin
from kairos.core.orchestrator_parts.context import OrchContextMixin
from kairos.core.orchestrator_parts.loopctl import OrchLoopControlMixin
from kairos.core.orchestrator_parts.introspect import OrchIntrospectMixin


class Orchestrator(OrchLifecycleMixin, OrchWiringMixin, OrchReferenceMixin, OrchContextMixin, OrchLoopControlMixin, OrchIntrospectMixin):
    """Bootstraps projects and runs Coder <-> Reviewer loops."""

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
        # R38.6.4: per-project set of project_ids we've already
        # tried-and-failed to attach agents for in this process.
        # get_project's lazy retry checks this so we don't spam
        # the log on every request when a project is stuck without
        # a coder. The set is in-memory only — a backend restart
        # clears it, which is fine because the user will see the
        # 503 → know to refresh Settings → the next request will
        # log a fresh attempt.
        self._attach_failures: set = set()
        #: Live MCP registries keyed by project id. A registry owns
        #: subprocesses, so a project must never accumulate a second one:
        #: reusing by id keeps the child-process count flat even when the
        #: ``Project`` object is rebuilt (a cache-miss rehydrate) or the
        #: toolset is otherwise rebuilt. Cleared on ``close``/delete.
        #: (Also reachable via ``getattr`` — some tests build the
        #: orchestrator with ``__new__`` and skip this initialiser.)
        self._mcp_registries: dict = {}
        #: The effective-MCP-config fingerprint each project's live registry was
        #: built from, keyed by project id. Compared on every attach so a real
        #: edit of ``.kairos/mcp.yaml`` (or the user scope) triggers a
        #: close-then-swap reload, while an unchanged config keeps the registry
        #: (and its subprocesses) exactly as they are. (Reachable via
        #: ``getattr`` — some tests build the orchestrator with ``__new__``.)
        self._mcp_fingerprints: dict = {}
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

    def _load_projects(self):
        # R38.6.4: never let a single bad row kill the whole load.
        # If Project construction or agent creation fails for one
        # project, log it and continue with the next — the user can
        # still hit that project via get_project's self-heal path.
        loaded = 0
        skipped = 0
        for row in self._db.load_projects():
            try:
                workspace = Path(row["workspace"])
                if not workspace.is_absolute():
                    # Legacy rows stored CWD-relative workspace paths
                    # (e.g. "workspace/ab12"). Resolve them against the
                    # repo root so projects land in the same place no
                    # matter which directory launched the backend.
                    from kairos.config.settings import settings
                    workspace = (settings.workspace_dir.parent / workspace).resolve()
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
                try:
                    self._create_agents(project)
                except Exception:
                    logger.debug("_load_projects: agent creation failed for %s",
                                 row["id"], exc_info=True)
                loaded += 1
            except Exception:
                skipped += 1
                logger.warning("_load_projects: skipping %s (load failure)",
                               row.get("id"), exc_info=True)
        if loaded or skipped:
            logger.info("_load_projects: loaded=%d skipped=%d",
                        loaded, skipped)

    def create_project(self, name: str, description: str, work_dir: str = "",
                       *, project_id: str = "") -> Project:
        project_id = project_id or uuid.uuid4().hex[:8]
        workspace = self.workspace_base / project_id
        workspace.mkdir(parents=True, exist_ok=True)
        project = Project(project_id, name, description, workspace, work_dir, db=self._db)
        self._projects[project_id] = project
        self._db.save_project(project)
        self._create_agents(project)
        return project

    def attach_project(self, repo: str, *, name: str = "",
                       persist: bool = True) -> Project:
        """Find-or-create the single worker bound to a repository.

        A main agent dispatching a second task must land on the same session
        as the first, otherwise the worker is a colleague who forgets
        everything between tasks. The binding (repo → project id) is written
        once under ``.kairos/worker.json`` and reused from then on; because
        projects are rehydrated from the database at startup, the same id
        means the same notes, preferences, checkpoints and history.

        Unlike ``create_project`` this is idempotent: calling it twice for one
        repo returns the same project rather than a second one.
        """
        from kairos import worker_identity

        binding = worker_identity.bind(repo, name=name, persist=persist)
        project = self._projects.get(binding.project_id)
        if project is not None:
            # Keep the row honest if the working copy moved.
            resolved = str(Path(repo).expanduser().resolve())
            if resolved and project.work_dir != resolved:
                project.work_dir = resolved
                self._db.save_project(project)
            logger.debug("attached to existing worker %s for %s",
                         binding.project_id, repo)
            return project

        project = self.create_project(
            name=binding.name or Path(repo).name or "worker",
            description=f"Worker bound to {binding.repo}",
            work_dir=str(Path(repo).expanduser().resolve()),
            project_id=binding.project_id,
        )
        logger.info("bound worker %s to %s", binding.project_id, binding.repo)
        return project

    def _create_agents(self, project: Project):
        # R38.6.4 packaging: Coder/Reviewer are lazy-loaded at module
        # level via __getattr__, but that mechanism only fires on
        # attribute access (module.Coder) — NOT on the bare-name lookup
        # inside this method body, which raises NameError. Import them
        # locally here (same pattern as _instantiate_specialists) so the
        # role classes resolve regardless of the module-level loading
        # strategy.
        from kairos.agents.roles import Coder, Reviewer
        effective_root = project.work_dir or str(project.workspace)
        Path(effective_root).mkdir(parents=True, exist_ok=True)

        # Optionally isolate each role in its own git worktree.
        # Only when the project root is itself a git repo. Failure
        # is logged + recorded on the runtime, not raised — a
        # non-git or unwritable project still gets a working agent.
        coder_root = effective_root
        reviewer_root = effective_root
        # A re-attach (an MCP-config reload, a cache-miss rehydrate) must reuse
        # the worktrees the project already owns. Creating a second pair would
        # orphan the first pair's checkouts on disk.
        existing_coder_wt = project.runtime.coder_worktree
        existing_reviewer_wt = project.runtime.reviewer_worktree
        if existing_coder_wt is not None or existing_reviewer_wt is not None:
            if existing_coder_wt is not None:
                coder_root = str(existing_coder_wt.path)
            if existing_reviewer_wt is not None:
                reviewer_root = str(existing_reviewer_wt.path)
        else:
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
            CodeSearchTool(allowed_root=coder_root),
            GitTool(allowed_root=coder_root),
            TerminalTool(allowed_cwd=coder_root),
            WebFetchTool(),
            WebSearchTool(),
            # Past-session recall: the Coder (and the single-turn chat path,
            # which runs on this same agent) could only ever see the current
            # session, so 「上次我们是怎么做的」 had no answer. This reads the
            # stored messages of *other* sessions, read-only, and quotes them
            # with their session title/id and time.
            HistorySearchTool(allowed_root=coder_root, project_id=project.id),
            # The schema the model needs in order to emit the ``write_todos``
            # call the agent loop intercepts (and applies to its plan tracker).
            # Without it in the tool list the whole plan-panel mechanism is
            # unreachable: the LLM never calls a tool it has not been told about.
            WriteTodosTool(allowed_root=coder_root),
            # P0-5 general-purpose tools (the credential-free batch). The three
            # read-only tools can only read inside the coder root; python_run
            # declares EXEC_PROCESS, so the sentinel's ladder asks for it.
            DataAnalyzeTool(allowed_root=coder_root),
            XlsxReadTool(allowed_root=coder_root),
            DocReadTool(allowed_root=coder_root),
            PythonRunTool(allowed_root=coder_root),
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
        # R38.6.4 background sub-agents: ``spawn_subagent`` returns a handle
        # immediately, and these two read-only tools are how the model polls it
        # and collects the report. Without them the handle was a dead end — the
        # spawn schema and the spawn result both pointed at tools that were
        # never registered. They read the registry through the spawn tool's
        # parent_agent / project_id, so a handle from another session is not
        # reachable.
        coder_tools.append(SubagentStatusTool(spawn_tool=subagent_tool,
                                              allowed_root=coder_root))
        coder_tools.append(SubagentResultTool(spawn_tool=subagent_tool,
                                              allowed_root=coder_root))

        # The two capabilities the agent was promised and never had. Both
        # modules shipped with tests and no caller, and the bundled
        # computer-use skill already told every model it had the tool.
        # Non-fatal on purpose: a machine without Playwright must still get
        # a working Coder, just without the browser.
        try:
            from kairos.tools.browser_tool import BrowserTool
            coder_tools.append(BrowserTool(allowed_root=coder_root,
                                           project_id=project.id))
        except Exception as e:  # noqa: BLE001
            logger.debug("browser tool unavailable: %s", e)
            project.runtime.attach_errors.append(f"browser_tool: {e}")
        try:
            from kairos.tools.computer_tool import ComputerTool
            coder_tools.append(ComputerTool(allowed_root=coder_root))
        except Exception as e:  # noqa: BLE001
            logger.debug("computer-use tool unavailable: %s", e)
            project.runtime.attach_errors.append(f"computer_tool: {e}")

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

        # Apply the project's approval mode to the gate. Until this existed the
        # setting was written by the API and read by nobody: `get_sentinel()`
        # always built a SUGGEST gate, so "full-auto" in the UI changed nothing
        # about what the agent asked for. The gate is one per process and may
        # serve several projects, so the mode follows the attached project.
        try:
            from kairos.sentinel import get_sentinel
            from kairos.approval import DEFAULT_MODE
            metadata = getattr(project, "metadata", None)
            stored = ""
            if isinstance(metadata, dict):
                stored = metadata.get("approval_mode") or ""
            if not stored:
                stored = getattr(project.runtime, "approval_mode", "") \
                    or DEFAULT_MODE.value
            project.runtime.approval_mode = get_sentinel().set_mode(stored).value
        except Exception as e:  # noqa: BLE001
            logger.warning("approval mode wiring failed for %s: %s", project.id, e)
            project.runtime.attach_errors.append(f"approval_mode: {e}")

        reviewer_tools = [
            FileReadTool(allowed_root=reviewer_root),
            GrepTool(allowed_root=reviewer_root),
            FindTool(allowed_root=reviewer_root),
            CodeSearchTool(allowed_root=reviewer_root),
            GitTool(allowed_root=reviewer_root),
            TerminalTool(allowed_cwd=reviewer_root),
            # P0-5 read-only general tools: the Reviewer verifies data and
            # documents too, and these can only read inside its root. The
            # interpreter stays with the Coder (the Reviewer already has the
            # shell for the checks it needs).
            DataAnalyzeTool(allowed_root=reviewer_root),
            XlsxReadTool(allowed_root=reviewer_root),
            DocReadTool(allowed_root=reviewer_root),
        ]

        # The Reviewer gets the browser too: "does it actually render" is a
        # verification question, and it cannot be answered from the diff.
        # Desktop control stays with the Coder.
        try:
            from kairos.tools.browser_tool import BrowserTool
            reviewer_tools.append(BrowserTool(allowed_root=reviewer_root,
                                              project_id=project.id))
        except Exception as e:  # noqa: BLE001
            logger.debug("reviewer browser tool unavailable: %s", e)

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

        # Wire the Coder / Reviewer. NEVER leave project.coder / .reviewer
        # None: a transient or unknown-model provider error must not cripple
        # the project with "No Coder agent wired" (503). `create_provider`
        # already falls back to an OpenAI-compatible provider for unknown
        # names; here we also catch any residual construction error, log it,
        # and retry once with the router's default provider so the project
        # always ends up with a working agent.
        def _wire(role: str, role_cls, tools, preferred):
            try:
                return self._make_agent(
                    project.id, role, role_cls, preferred, tools,
                    self.message_bus, yaml_prompts,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent wiring failed for %s.%s: %s — "
                               "retrying with default provider",
                               project.id, role, exc)
                project.runtime.attach_errors.append(f"{role}: {exc}")
                try:
                    default_provider = self.model_router.get_provider_for_role("default")
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("default provider fallback failed: %s", exc2)
                    raise
                return self._make_agent(
                    project.id, role, role_cls, default_provider, tools,
                    self.message_bus, yaml_prompts,
                )

        coder_provider = self.model_router.get_provider_for_role("coder")
        reviewer_provider = self.model_router.get_provider_for_role("reviewer")

        project.coder = _wire("coder", Coder, coder_tools, coder_provider)
        project.reviewer = _wire("reviewer", Reviewer, reviewer_tools, reviewer_provider)

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
        # once a second; if no project_dir is set we skip it. A re-attach
        # stops the previous watcher first, so consecutive attaches never
        # leave two pollers running for the same project.
        try:
            prev_watcher = project.runtime.skills_watcher
            if prev_watcher is not None:
                try:
                    prev_watcher.stop_sync()
                except Exception as e:  # noqa: BLE001
                    logger.debug("previous skills watcher stop failed: %s", e)
                project.runtime.skills_watcher = None
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

    def _attach_mcp(self, project: Project, work_dir: str) -> List[Any]:
        """Attach this project's MCP servers, reloading only on a real change.

        Returns the list of MCP-sourced tools (Kairos BaseTool instances) ready
        to append to a role's toolset. The registry is stored on
        ``project.runtime.mcp_registry`` so it can be closed on shutdown.

        One live registry per project. A rebuild is free while the effective
        config is unchanged; a *reload* (close-then-swap) happens only when the
        fingerprint of the user-editable config files actually moved. The old
        code built a brand-new ``McpRegistry`` on every attach and then
        overwrote ``runtime.mcp_registry`` with it, orphaning the previous
        registry's MCP subprocesses (~100MB each) with nothing left to
        ``close_all`` them — the process leak (an instance spawned new
        ``--mcp-serve`` children every few seconds and never reaped the old
        ones). Reuse keeps the child count flat; the fingerprint check restores
        the "edit mcp.yaml and it takes effect" behaviour that pure reuse had
        removed.

        Two sources are checked for the existing registry: the runtime handle on
        this Project object, and a process-level map keyed by project id (so a
        rebuilt Project — a cache-miss rehydrate — still finds its registry
        instead of spawning a second set).
        """
        from kairos.mcp_client import McpRegistry, should_defer_start
        if not work_dir:
            return []
        regs = getattr(self, "_mcp_registries", None)
        if regs is None:
            regs = {}
            self._mcp_registries = regs
        fps = getattr(self, "_mcp_fingerprints", None)
        if fps is None:
            fps = {}
            self._mcp_fingerprints = fps

        fingerprint = self._mcp_config_fingerprint(work_dir)

        existing = regs.get(project.id)
        if existing is None:
            existing = getattr(project.runtime, "mcp_registry", None)

        if existing is not None:
            stored = fps.get(project.id)
            if stored is None or stored == fingerprint:
                # Unchanged (or provenance unknown): reuse. No new children, same
                # tool set, no growth. Record the fingerprint so a later edit is
                # still noticed.
                fps[project.id] = fingerprint
                regs[project.id] = existing
                project.runtime.mcp_registry = existing
                return list(existing.all_tools())
            # The effective config changed on disk. Bounded reload: the old
            # registry is closed BEFORE the replacement is built and started, so
            # two live sets never coexist.
            reloaded = self._reload_mcp_registry(
                project, existing, work_dir, fingerprint)
            if reloaded is None:
                # close_all raised while stopping the old registry. Building a
                # second live registry would leave the old children orphaned
                # with nothing pointing at them — exactly the leak — so keep the
                # old one and leave a trace. The change is deferred, not lost:
                # the next attach sees the same fingerprint gap and retries.
                regs[project.id] = existing
                project.runtime.mcp_registry = existing
                note = ("mcp: reload deferred — closing the previous registry "
                        "for this project failed")
                if note not in project.runtime.attach_errors:
                    project.runtime.attach_errors.append(note)
                return list(existing.all_tools())
            return reloaded

        reg = McpRegistry()
        try:
            reg.load(project_dir=Path(work_dir))
        except Exception as e:  # noqa: BLE001
            logger.debug("MCP load skipped for %s: %s", project.id, e)
            return []
        self._start_registry(reg, project, should_defer_start)
        project.runtime.mcp_registry = reg
        regs[project.id] = reg
        fps[project.id] = fingerprint
        return list(reg.all_tools())

    def _mcp_config_fingerprint(self, work_dir: str) -> str:
        """Fingerprint of the user-editable MCP config under ``work_dir``.

        Best-effort: any error degrades to ``""`` so a fingerprint problem can
        never block an attach. The heavy part (hashing the files) is cached in
        ``mcp_client`` on ``(mtime_ns, size)``, so the hot path only pays a
        ``stat`` per file and only re-reads a file that actually moved.
        """
        if not work_dir:
            return ""
        try:
            from kairos.mcp_client import mcp_config_fingerprint
            return mcp_config_fingerprint(project_dir=Path(work_dir))
        except Exception as e:  # noqa: BLE001
            logger.debug("mcp config fingerprint failed for %s: %s", work_dir, e)
            return ""

    @staticmethod
    def _start_registry(reg, project: Project, should_defer_start) -> None:
        """Run or schedule ``reg.start_all()`` to match the caller's context.

        A running loop (a request) schedules it — a request must not block on
        server startup. With no loop anywhere else (a script, the startup path)
        it starts inline, or defers when the app is about to open a port (see
        :func:`kairos.mcp_client.should_defer_start`). Never raises.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(reg.start_all())
                return
            if should_defer_start():
                reg.defer_start()
                return
            loop.run_until_complete(reg.start_all())
        except RuntimeError:
            if should_defer_start():
                reg.defer_start()
                return
            try:
                asyncio.run(reg.start_all())
            except Exception as e:  # noqa: BLE001
                logger.debug("MCP start_all failed for %s: %s", project.id, e)
        except Exception as e:  # noqa: BLE001
            logger.debug("MCP start scheduling failed for %s: %s", project.id, e)

    @staticmethod
    def _run_coroutine(coro) -> None:
        """Run ``coro`` to completion on this thread (no loop is running here).

        Some embedders leave a closed loop as the thread's current loop, so fall
        back to ``asyncio.run`` when that one is unusable.
        """
        try:
            loop = asyncio.get_event_loop()
        except Exception:  # noqa: BLE001
            loop = None
        if loop is not None and not loop.is_closed():
            try:
                if not loop.is_running():
                    loop.run_until_complete(coro)
                    return
            except RuntimeError as e:
                # A closed / unusable current loop: fall through to a fresh one
                # instead of failing the reload.
                logger.debug("event loop unusable for MCP reload (%s); "
                             "starting a fresh one", e)
        asyncio.run(coro)

    def _reload_mcp_registry(self, project: Project, old, work_dir: str,
                             fingerprint: str) -> Optional[List[Any]]:
        """Close ``old`` then build + start a replacement, in that order.

        Returns the replacement's tools, or ``None`` when ``old`` could not be
        closed — the caller then keeps it, never a second live registry.

        Ordering is the whole point: if the old subprocesses are not reaped
        before the new ones spawn, a reload is just the original churn with
        extra steps.
        """
        from kairos.mcp_client import McpRegistry, should_defer_start
        try:
            loop = asyncio.get_event_loop()
            running = loop.is_running()
        except RuntimeError:
            loop = None
            running = False

        if running:
            # Inside a request: a fresh coroutine cannot be run to completion
            # here, so the whole close-then-swap runs as ONE task on this loop.
            # One task (not a fire-and-forget close plus a separate start) is
            # what keeps the order from interleaving the other way.
            #
            # Record the fingerprint now so a second request arriving mid-swap
            # does not queue another reload; restore it if the swap fails so a
            # later attach can retry.
            prev_fp = self._mcp_fingerprints.get(project.id)
            self._mcp_fingerprints[project.id] = fingerprint

            async def _swap() -> None:
                try:
                    await old.close_all()
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "mcp: reload aborted for %s — close_all failed: %s",
                        project.id, e)
                    self._mcp_fingerprints[project.id] = prev_fp
                    return
                reg = McpRegistry()
                try:
                    reg.load(project_dir=Path(work_dir))
                except Exception as e:  # noqa: BLE001
                    logger.debug("MCP reload load skipped for %s: %s",
                                 project.id, e)
                self._mcp_registries[project.id] = reg
                project.runtime.mcp_registry = reg
                await reg.start_all()

            loop.create_task(_swap())
            return []

        # No running loop (a script, a test, the startup path): do it inline so
        # the caller sees the finished swap.
        try:
            self._run_coroutine(old.close_all())
        except Exception as e:  # noqa: BLE001
            logger.warning("mcp: reload aborted for %s — close_all failed: %s",
                           project.id, e)
            return None
        reg = McpRegistry()
        try:
            reg.load(project_dir=Path(work_dir))
        except Exception as e:  # noqa: BLE001
            logger.debug("MCP reload load skipped for %s: %s", project.id, e)
        self._mcp_registries[project.id] = reg
        project.runtime.mcp_registry = reg
        self._mcp_fingerprints[project.id] = fingerprint
        self._start_registry(reg, project, should_defer_start)
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

    def _attach_agents_once(self, project: Project) -> None:
        """Wire a project's Coder/Reviewer at most once per process.

        ``get_project`` sits on the request hot path, so an attach that
        *neither raises nor actually wires the project* must not be re-run on
        every request: each re-run rebuilds the whole MCP registry and spawns
        a fresh set of MCP subprocesses (see ``_attach_mcp``), and the request
        latency with it.

        The guard keys on the **result**, not on whether ``_create_agents``
        raised. Success means ``project.coder`` is set. A return that leaves
        ``coder`` None (an attach that silently did nothing) stays in
        ``_attach_failures`` so it is retried once per process — the
        documented contract — not once per request. When a later attach *is*
        genuinely wired the marker is cleared, so a subsequent transient
        failure can retry again.
        """
        pid = project.id
        if project.coder is not None:
            # Already wired. A live edit of the effective MCP config must still
            # take effect without a restart, so the hot path checks the cheap
            # fingerprint once per request. Only an actual change re-attaches;
            # an unchanged config never rebuilds anything (that per-request
            # churn was the subprocess leak).
            if self._mcp_config_changed(project):
                self._reattach_for_mcp_change(project)
            return
        if pid in self._attach_failures:
            return
        self._attach_failures.add(pid)
        try:
            self._create_agents(project)
        except Exception:  # noqa: BLE001
            logger.warning(
                "get_project: agent attach failed for %s "
                "(will not retry until backend restart); attach_errors: %s",
                pid,
                getattr(project.runtime, "attach_errors", []),
            )
            return
        if project.coder is not None:
            # Genuinely wired: drop the marker so a future transient
            # failure can retry again.
            self._attach_failures.discard(pid)
        # else: returned without wiring the project — keep the marker so we
        # do not rebuild the registry on every request.

    def _mcp_config_changed(self, project: Project) -> bool:
        """True when the effective MCP config moved since the last attach.

        Cheap by design: the fingerprint is cached on ``(mtime_ns, size)`` in
        ``mcp_client``, so an unchanged config costs one ``stat`` per config
        file, and only a file that actually moved is re-read. Returns False when
        no fingerprint has been recorded yet — a project we never attached has
        nothing to compare against, and the attach itself will record one.
        """
        stored = getattr(self, "_mcp_fingerprints", {}).get(project.id)
        if stored is None:
            return False
        work_dir = project.work_dir or str(project.workspace)
        return self._mcp_config_fingerprint(work_dir) != stored

    def _reattach_for_mcp_change(self, project: Project) -> None:
        """Re-wire a live project after its effective MCP config changed.

        Bounded: only reached when the fingerprint actually moved. The reload
        itself is the close-then-swap inside ``_attach_mcp``; running it through
        ``_create_agents`` is what hands the *new* tools to the agents (they are
        baked in at construction). ``_create_agents`` is re-entrant here: it
        reuses the project's worktrees and stops the previous skills watcher.
        """
        logger.info("mcp: effective config changed for %s — reloading the "
                    "registry and re-wiring its agents", project.id)
        try:
            self._create_agents(project)
        except Exception:  # noqa: BLE001
            logger.warning("mcp: re-attach after a config change failed for %s; "
                           "attach_errors: %s", project.id,
                           getattr(project.runtime, "attach_errors", []))

    def get_project(self, project_id: str) -> Optional[Project]:
        """Look up a project by id, self-healing from the DB.

        If the in-memory cache misses (e.g. after a backend restart that
        raced with the user opening a tab, or after `_load_projects`
        failed for a specific row), try to re-hydrate from the DB on
        demand. This makes the API resilient to stale project_id
        references the frontend may have cached.

        Also lazy-retries agent creation if a previous attempt failed
        (e.g. transient MCP / provider init error), at most once per
        process — see ``_attach_agents_once``.
        """
        p = self._projects.get(project_id)
        if p is not None:
            self._attach_agents_once(p)
            return p
        # Self-heal: reload from DB and instantiate the Project in memory
        # so subsequent lookups are fast again.
        try:
            rows = self._db.load_projects(include_archived=True)
        except Exception:
            return None
        for row in rows:
            if row.get("id") != project_id:
                continue
            workspace = Path(row["workspace"])
            if not workspace.exists():
                workspace.mkdir(parents=True, exist_ok=True)
            work_dir = row.get("work_dir") or str(workspace)
            project = Project(
                project_id=project_id,
                name=row.get("name", project_id),
                description=row.get("description", ""),
                workspace=workspace,
                work_dir=work_dir,
                db=self._db,
            )
            project.requirements = row.get("requirements", "")
            project.status = row.get("status", "active")
            self._projects[project_id] = project
            # Same once-per-process guard as the cache-hit path: a rehydrate
            # that keeps missing must not rebuild the MCP registry per request.
            self._attach_agents_once(project)
            return project
        return None

    def list_projects(self) -> List[Project]:
        return list(self._projects.values())

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
        # Drop the process-level handle too, otherwise a later re-attach for
        # the same id would hand back a registry whose children just closed.
        regs = getattr(self, "_mcp_registries", None)
        if regs is not None:
            regs.pop(project.id, None)
        fps = getattr(self, "_mcp_fingerprints", None)
        if fps is not None:
            fps.pop(project.id, None)
        if rt.mcp_registry is not None:
            try:
                await rt.mcp_registry.close_all()
            except Exception as e:  # noqa: BLE001
                logger.debug("MCP close_all failed: %s", e)
            rt.mcp_registry = None

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

        ref_digest = self.build_reference_digest(project_id, query=requirement)
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
        # Review calibration: when enabled, a Reviewer verdict without
        # real test evidence can never approve the round (the loop
        # runner clamps it). Opt-in via loop_config.require_test_evidence.
        session.require_test_evidence = bool(
            loop_cfg.get("require_test_evidence", False)
        )
        # Difficulty-aware routing: when enabled, trivial requirements
        # run the Coder on the cheap "fast" tier for this loop, while
        # normal/complex work keeps the role's configured model. Best
        # effort — a routing failure must never block the loop.
        if loop_cfg.get("difficulty_routing", False):
            try:
                from kairos.llm.model_router import resolve_task_tier
                tier = resolve_task_tier(requirement)
                if tier == "fast" and getattr(session, "coder", None) is not None:
                    fast_provider = self.model_router.get_provider_for_task_strict("fast")
                    if fast_provider is not None:
                        # Swap atomically and keep _llm_config in sync so
                        # state.model reports the model actually in use.
                        session.coder._llm = fast_provider
                        cfg = getattr(fast_provider, "config", None)
                        if cfg is not None:
                            session.coder._llm_config = cfg
                        await self.message_bus.publish(Message(
                            sender="orchestrator", topic="loop.difficulty_routed",
                            content="Trivial requirement detected: Coder routed to fast tier.",
                            msg_type="text",
                            metadata={"project_id": project_id, "tier": tier},
                        ))
            except Exception:
                logger.debug("difficulty routing failed (non-fatal)", exc_info=True)
        project.loop_session = session
        project.loop_task = asyncio.create_task(
            run_loop(session, requirement, unbounded=unbounded),
            name=f"loop-{project_id}",
        )
        project.loop_task.add_done_callback(
            lambda t: self._on_loop_done(project_id, t)
        )
        return session.session_id
