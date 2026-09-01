"""Plan Mode (R38.6 §34, inspired by the the the plan-mode pattern Plan Mode).

When the user kicks off a multi-step task, the orchestrator
must first produce a TODO plan, present it to the user, and
wait for explicit approval before starting execution. The
user can:

  - **approve** — proceed as planned
  - **edit** — modify the plan, then approve
  - **reject** — cancel the task entirely (with feedback)

Why?
  - Long-horizon agent tasks can waste 10-20 minutes of
    compute + dozens of API calls if the user disagrees
    with the approach. Plan Mode catches this BEFORE any
    execution.
  - Same UX as the the the plan-mode pattern's Plan Mode, the agentic CLI's
    "shift+tab to plan" mode, and Cursor's Plan/Build tabs.

Storage
-------
A plan is a `Plan` object containing an ordered list of
`PlanStep` records. Plans live in the project's
`.kairos/plans/<plan_id>.json` so the user can revisit
them, and the Web UI can show the plan as a checklist
during execution.

Lifecycle
---------
  1. User clicks "Run" on a long task
  2. Orchestrator spawns a `PlannerAgent` that returns
     a Plan (no tool calls, just thinking)
  3. Web UI shows the plan in a modal with
     approve / edit / reject buttons
  4. User approves → orchestrator runs the plan step-by-
     step, marking each step as in_progress / done / failed
  5. User rejects → plan is archived with feedback note
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
    """One step in a plan."""
    id: str
    title: str
    detail: str = ""
    # Optional tool hint — what tool the agent should use
    tool_hint: Optional[str] = None
    # Status: pending | in_progress | done | failed | skipped
    status: str = "pending"
    # What actually happened (filled when status != pending)
    result: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass
class Plan:
    """A multi-step plan presented to the user for approval."""
    id: str
    project_id: str
    task: str                       # the original user task
    steps: List[PlanStep] = field(default_factory=list)
    # Lifecycle: draft | approved | rejected | executing | done
    status: str = "draft"
    created_at: float = field(default_factory=time.time)
    approved_at: Optional[float] = None
    feedback: Optional[str] = None   # user feedback on reject / edit
    # Optional model that proposed the plan (for audit)
    proposed_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Plan":
        return cls(
            id=d["id"],
            project_id=d["project_id"],
            task=d["task"],
            steps=[PlanStep(**s) for s in d.get("steps", [])],
            status=d.get("status", "draft"),
            created_at=d.get("created_at", time.time()),
            approved_at=d.get("approved_at"),
            feedback=d.get("feedback"),
            proposed_by=d.get("proposed_by"),
        )


class PlanStore:
    """Persist plans to disk as JSON, one file per plan.

    Stored at ``<work_dir>/.kairos/plans/<plan_id>.json`` so
    they stay with the project (rsync / git friendly).
    """

    def __init__(self, work_dir: Path):
        self.root = Path(work_dir) / ".kairos" / "plans"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: str) -> Path:
        return self.root / f"{plan_id}.json"

    def save(self, plan: Plan) -> None:
        with open(self._path(plan.id), "w", encoding="utf-8") as f:
            json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)

    def get(self, plan_id: str) -> Optional[Plan]:
        p = self._path(plan_id)
        if not p.exists():
            return None
        try:
            return Plan.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("plan %s read failed: %s", plan_id, exc)
            return None

    def list_for_project(self, project_id: str) -> List[Plan]:
        out: List[Plan] = []
        for f in self.root.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("project_id") == project_id:
                    out.append(Plan.from_dict(d))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        # Newest first
        out.sort(key=lambda p: p.created_at, reverse=True)
        return out


# ---------------------------------------------------------------------------
# Plan generation — ask the LLM to lay out a TODO before any tool runs.
# ---------------------------------------------------------------------------

PLAN_GENERATION_PROMPT = """You are a senior engineer planning a coding task.

The user wants:
{user_task}

Project context: {project_context}

Break the task into 3-10 concrete steps. For each step, give:
- `title`: short imperative line (≤ 60 chars), e.g. "Add /api/projects endpoint"
- `detail`: 1-2 sentences explaining WHAT this step does and WHY
- `tool_hint`: which tool the agent should call for this step. Pick from:
  - "read"  (read a file)
  - "edit"  (modify one file)
  - "multi_edit" (modify several files at once)
  - "terminal" (run a shell command)
  - "grep" / "find" (search)
  - "webfetch" (HTTP call)
  - "browser" (Playwright browser)
  - "subagent" (delegate)
  - "" (no specific tool — just a planning step)

Respond with a JSON array. No commentary, no markdown fences.

Example response:
[
  {{"title": "Read project structure", "detail": "Get a lay of the land", "tool_hint": "find"}},
  {{"title": "Add config module", "detail": "Create kairos/config.py with the env-loader", "tool_hint": "edit"}}
]"""


def build_plan_from_llm(user_task: str, project_context: str,
                         llm_complete_fn, proposed_by: str = "planner") -> Plan:
    """Ask the LLM to lay out a plan. Returns a draft Plan.

    `llm_complete_fn(prompt: str) -> str` is a callable that
    sends a prompt to the active LLM and returns the raw
    completion. It MUST return a JSON array (the function
    falls back to a 1-step generic plan if parsing fails).
    """
    plan_id = uuid.uuid4().hex[:12]
    raw = llm_complete_fn(
        PLAN_GENERATION_PROMPT.format(
            user_task=user_task, project_context=project_context,
        )
    )
    steps: List[PlanStep] = []
    # Try to parse JSON — strip markdown fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        items = json.loads(cleaned)
        if isinstance(items, list):
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                steps.append(PlanStep(
                    id=f"step-{i+1:02d}",
                    title=str(item.get("title", f"Step {i+1}"))[:120],
                    detail=str(item.get("detail", ""))[:500],
                    tool_hint=item.get("tool_hint") or None,
                ))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("plan generation JSON parse failed: %s", exc)
        steps = []  # fall through to fallback

    if not steps:
        # Fallback: a single generic step. Better than nothing.
        steps = [PlanStep(
            id="step-01",
            title="Execute the task",
            detail=user_task[:300],
            tool_hint=None,
        )]

    return Plan(
        id=plan_id,
        project_id="",   # set by caller
        task=user_task,
        steps=steps,
        proposed_by=proposed_by,
    )


def mark_step(plan: Plan, step_id: str, status: str,
              result: Optional[str] = None) -> None:
    """Mutate `plan` to mark one step's status. The caller
    is responsible for persisting via `plan_store.save(plan)`."""
    for s in plan.steps:
        if s.id == step_id:
            s.status = status
            if result is not None:
                s.result = result[:500]
            now = time.time()
            if status == "in_progress" and s.started_at is None:
                s.started_at = now
            elif status in ("done", "failed", "skipped"):
                s.finished_at = now
            return
    logger.warning("step %s not found in plan %s", step_id, plan.id)
