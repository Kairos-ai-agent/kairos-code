"""Adapters: make the existing Coder/Reviewer *one implementation* of the
skeleton, and provide a domain-neutral LLM worker.

Nothing here rewrites the Coder or Reviewer. ``CoderWorker`` wraps whatever
object implements the Coder's ``run(task, plan_mode=)`` contract (the real
``kairos.agents.roles.Coder`` included); ``ReviewerVerifier`` wraps the
Reviewer and maps its verdict onto a structured :class:`Verdict`. Both are
thin -- no loop semantics change.
"""
from __future__ import annotations

import inspect
import os
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Optional

from kairos.skeleton.contracts import (
    Task,
    Verdict,
    Verifier,
    Worker,
    WorkerResult,
    Workspace,
)

# The loop's approval bar; the Reviewer adapter uses it to decide whether a
# "no bugs" verdict with a score clears the gate as a pass.
APPROVE_SCORE_THRESHOLD = 85


@contextmanager
def _in_workspace(root: Optional[str]):
    """Run the enclosed block with ``root`` as the process working directory.

    The real Coder resolves relative paths against *its* cwd, so an adapter that
    only stashed ``workspace.root`` in the task context would let the agent edit
    the process's tree instead of the workspace it was handed. Entering the
    workspace makes ``CoderWorker(real_coder).run(RepoWorkspace(X))`` actually
    act on **X**. The previous cwd is restored on exit. Note this is
    process-global: the skeleton driver runs one worker at a time.
    """
    if not root or not os.path.isdir(root):
        yield
        return
    previous = os.getcwd()
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(previous)


def _bind_agent_to(agent: Any, root: str) -> None:
    """Best-effort: point common cwd-carrying attributes at the workspace root.

    Agents that expose ``work_dir`` / ``cwd`` get updated; agents that derive
    their working directory from the process cwd are covered by
    :func:`_in_workspace` instead. Never raises.
    """
    for attr in ("work_dir", "cwd"):
        try:
            if hasattr(agent, attr):
                setattr(agent, attr, root)
        except Exception:
            pass



def verdict_from_review(
    review: dict, *, verifier: str = "reviewer", threshold: int = APPROVE_SCORE_THRESHOLD
) -> Verdict:
    """Translate a loop-style review dict into a structured :class:`Verdict`.

    The Reviewer speaks ``{approve, score, issues, summary, tests_evidence}``;
    the skeleton wants per-criterion evidence. Map the model's opinion to
    data: one evidence row for the approve flag, one per issue, one for test
    evidence when the Reviewer reported running tests.
    """
    review = review or {}
    approve = bool(review.get("approve"))
    score = review.get("score")
    try:
        numeric = float(score) if score is not None else None
    except (TypeError, ValueError):
        numeric = None
    passed = approve and (numeric is None or numeric >= threshold)
    evidence = [{
        "criterion": "reviewer approves",
        "satisfied": approve,
        "detail": (review.get("summary") or "")[:400],
    }]
    if numeric is not None:
        evidence.append({
            "criterion": f"score >= {threshold}",
            "satisfied": numeric >= threshold,
            "value": numeric,
        })
    for issue in (review.get("issues") or [])[:20]:
        if isinstance(issue, dict):
            evidence.append({
                "criterion": "issue",
                "satisfied": False,
                "severity": issue.get("severity"),
                "file": issue.get("file", ""),
                "detail": (issue.get("description") or "")[:400],
            })
    evidence_raw = review.get("tests_evidence")
    if isinstance(evidence_raw, dict):
        evidence.append({
            "criterion": "tests evidence reported",
            "satisfied": bool(evidence_raw.get("ran")),
        })
    return Verdict(
        passed=passed,
        reason=(review.get("summary") or "")[:1000],
        score=numeric,
        evidence=evidence,
        verifier=verifier,
        meta={"approve": approve, "_failure_mode": review.get("_failure_mode")},
    )


class CoderWorker(Worker):
    """Adapt the existing Coder to :class:`Worker`.

    ``agent`` is any object with ``async run(task, plan_mode=False) -> str``
    -- the real ``kairos.agents.roles.Coder`` satisfies this, and so does a
    test stub, which is how the skeleton proves the Coder is *an*
    implementation rather than the only possibility.

    The workspace is bound at the **behaviour** level, not just in the task
    context: the agent runs with ``workspace.root`` as the working directory
    (and any ``work_dir``/``cwd`` attribute it exposes is pointed there too),
    so ``run(RepoWorkspace(X))`` really acts on ``X``.
    """

    name = "coder"

    def __init__(self, agent: Any, *, plan_mode: bool = False, project_id: str = ""):
        self.agent = agent
        self.plan_mode = plan_mode
        self.project_id = project_id

    async def run(self, workspace: Workspace, task: Task) -> WorkerResult:
        from kairos.agents.base import AgentTask

        root = str(getattr(workspace, "root", "") or "")
        agent_task = AgentTask(
            id=task.id,
            title=task.title,
            description=task.instruction,
            context={
                "project_id": self.project_id,
                "workspace_kind": workspace.kind,
                "workspace_root": root,
            },
        )
        try:
            _bind_agent_to(self.agent, root)
            with _in_workspace(root):
                output = await self.agent.run(agent_task, plan_mode=self.plan_mode) or ""
        except Exception as exc:  # a worker failure is data, not a crash
            return WorkerResult(
                ok=False, error=str(exc),
                summary=f"{getattr(self.agent, 'name', 'Coder')} crashed: {exc}",
                meta={"workspace_kind": workspace.kind, "workspace_root": root},
            )
        return WorkerResult(
            ok=True,
            output=output,
            artifacts=list(workspace.outputs()),
            summary=f"{getattr(self.agent, 'name', 'Coder')} produced {len(output)} chars",
            meta={"workspace_kind": workspace.kind, "plan_mode": self.plan_mode,
                  "workspace_root": root},
        )


class ReviewerVerifier(Verifier):
    """Adapt the existing Reviewer to :class:`Verifier`.

    This is the "rubric/reviewer" entry in the registry: the Reviewer is a
    model-as-verifier implementation, sitting *beside* the deterministic
    ``tests`` / ``assertion`` / ``tool_oracle`` verifiers, not replacing them.
    """

    name = "reviewer"

    def __init__(self, agent: Any, *, threshold: int = APPROVE_SCORE_THRESHOLD):
        self.agent = agent
        self.threshold = threshold

    async def verify(self, workspace: Workspace, task: Task, result: WorkerResult) -> Verdict:
        from kairos.agents.base import AgentTask
        from kairos.loop.reviewers import parse_review_verdict

        description = (
            "Decide whether the work below satisfies the task.\n\n"
            f"TASK:\n{task.instruction}\n\n"
            f"DELIVERABLE:\n{(result.output or '')[:8000]}"
        )
        agent_task = AgentTask(
            id=uuid.uuid4().hex[:8],
            title=f"Review {task.title}",
            description=description,
            context={"workspace_kind": workspace.kind},
        )
        try:
            raw = await self.agent.run(agent_task) or ""
        except Exception as exc:
            return Verdict(
                passed=False, verifier=self.name,
                reason=f"Reviewer crashed: {exc}",
                evidence=[{"criterion": "reviewer ran", "satisfied": False,
                           "detail": str(exc)}],
            )
        review = parse_review_verdict(raw)
        return verdict_from_review(review, verifier=self.name, threshold=self.threshold)


class PromptWorker(Worker):
    """A domain-neutral, LLM-driven worker.

    It does not touch git, tests, or files beyond the workspace: it renders the
    workspace's inputs (and the task) into a prompt, asks an injected
    ``generate`` callable for the deliverable, and emits it back as an
    artifact. The ``generate`` callable *is* the model -- a real provider in
    production, a deterministic fake in tests (no network needed).
    """

    name = "prompt"

    _PROMPT = (
        "TASK:\n{instruction}\n\n"
        "{context}\n\n"
        "Write the deliverable now. Cite every input you used as [[<name>]] "
        "on the line that relies on it, and end with a self-check line "
        "`SELF_CHECK: citations=<n>/<m> ok`."
    )

    def __init__(self, generate: Callable, name: str = "prompt"):
        self.generate = generate
        self.name = name

    async def run(self, workspace: Workspace, task: Task) -> WorkerResult:
        refs = task.inputs if task.inputs is not None else workspace.resources()
        prompt = self._PROMPT.format(
            instruction=task.instruction,
            context=workspace.as_context(refs),
        )
        try:
            text = self.generate(prompt)
            if inspect.isawaitable(text):
                text = await text
        except Exception as exc:
            return WorkerResult(ok=False, error=str(exc),
                                summary=f"LLM worker failed: {exc}")
        text = "" if text is None else str(text)
        ref = workspace.emit(task.output_name, text)
        return WorkerResult(
            ok=True,
            output=text,
            artifacts=[ref],
            summary=f"emitted {ref} ({len(text)} chars)",
            meta={"workspace_kind": workspace.kind, "inputs": list(refs)},
        )
