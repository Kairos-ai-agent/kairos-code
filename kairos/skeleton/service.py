"""Product entry for the general skeleton: run one non-code task end to end.

The router (:mod:`kairos.task_router`) decides that a task belongs on the
skeleton instead of the Coder <-> Reviewer loop; *this* module is what the
product then calls. It owns the plumbing so the HTTP route stays a thin
dispatcher:

* pick the workspace by kind -- ``DocSetWorkspace`` for ``docs``,
  ``RepoWorkspace`` for ``repo`` (the same choice the CLI makes);
* pick a worker -- a real model in production (``default_generator``), an
  injected fake in tests;
* pick a deterministic verifier -- ``citations`` for a document set,
  ``tests`` for a repo;
* run the existing :func:`kairos.skeleton.driver.run_task` (unchanged), which
  persists the run as JSON and publishes it to the message bus when one is
  given. The structured :class:`~kairos.skeleton.contracts.Verdict` is the
  return value's ``run.verdict`` -- the thing the API hands back to the caller
  and the record keeps.

Nothing here touches the code loop, the Coder/Reviewer, or the orchestrator's
behaviour; it is a new leaf that the loop never enters unless a request is
routed here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from kairos.skeleton.contracts import Task
from kairos.skeleton.driver import SkeletonRun, run_task
from kairos.skeleton.verifiers import (
    CitationConsistencyVerifier,
    ProjectTestsVerifier,
)
from kairos.skeleton.workspaces import DocSetWorkspace, RepoWorkspace

logger = logging.getLogger(__name__)

#: Default deliverable name for a routed task; the workspace decides where it
#: lands (``docs`` -> ``outputs/<name>``).
DEFAULT_OUTPUT_NAME = "report.md"


@dataclass
class SkeletonOutcome:
    """What one product-visible skeleton run produced."""

    run: SkeletonRun
    workspace_kind: str
    run_file: str = ""
    artifacts: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.artifacts is None:
            self.artifacts = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_kind": self.workspace_kind,
            "run_id": self.run.run_id,
            "outcome": self.run.outcome,
            "passed": self.run.passed,
            "verdict": self.run.verdict.to_dict(),
            "run_file": self.run_file,
            "artifacts": list(self.artifacts or []),
        }


def default_generator() -> Optional[Callable]:
    """A real-model ``generate(prompt) -> str``, or ``None`` if none is set.

    Mirrors how the CLI resolves a provider for the skeleton
    (``kairos.cli._intake_llm`` / ``_skeleton_provider_generator``): the
    ``coder`` role's provider, wrapped as an async ``generate``. Never raises;
    a missing model degrades to ``None`` so the caller can report it instead of
    crashing a request.
    """
    try:
        from kairos import config as _pkg_config
        from kairos.llm.base import LLMMessage
        from kairos.llm.model_router import ModelRouter

        cfg = Path(_pkg_config.__file__).parent / "models_config.yaml"
        provider = ModelRouter(config_path=cfg.resolve()).get_provider_for_role("coder")
        if provider is None:
            return None

        async def _generate(prompt: str) -> str:
            response = await provider.complete([LLMMessage(role="user", content=prompt)])
            return getattr(response, "content", "") or ""

        return _generate
    except Exception as exc:  # noqa: BLE001 - a missing model must not raise
        logger.info("no model provider for the skeleton (%s)", exc)
        return None


def build_workspace(kind: str, root: Any):
    """The workspace the CLI would build for ``kind`` at ``root``."""
    root = Path(str(root)).expanduser()
    if kind == "repo":
        return RepoWorkspace(root)
    return DocSetWorkspace(root)


def build_verifier(kind: str):
    """The deterministic verifier that fits ``kind``.

    ``tests`` for a repo (it abstains on a workspace with no tests, honestly),
    ``citations`` for a document set -- the cross-check that a report which
    read nothing cannot pass.
    """
    if kind == "repo":
        return ProjectTestsVerifier()
    return CitationConsistencyVerifier()


async def run_general_task(
    *,
    kind: str,
    root: Any,
    instruction: str,
    output_name: str = DEFAULT_OUTPUT_NAME,
    generate: Optional[Callable] = None,
    run_dir: Any = None,
    bus: Any = None,
) -> SkeletonOutcome:
    """Run one task through the skeleton and verify it; return the outcome.

    Raises ``RuntimeError`` when neither an injected ``generate`` nor a
    configured model is available -- the caller surfaces that honestly rather
    than pretending a task ran.
    """
    from kairos.skeleton.adapters import PromptWorker

    workspace = build_workspace(kind, root)
    generator = generate if generate is not None else default_generator()
    if generator is None:
        raise RuntimeError(
            "no model provider is configured for the general skeleton "
            "(set one, or supply a generator)"
        )
    worker = PromptWorker(generate=generator)
    verifier = build_verifier(kind)
    task = Task(instruction=instruction, output_name=output_name)

    if run_dir is None:
        run_dir = Path(str(root)).expanduser() / ".kairos" / "skeleton-runs"

    run = await run_task(worker, workspace, task, verifier, bus=bus, run_dir=run_dir)
    run_file = str(Path(run_dir) / f"skeleton-run-{run.run_id}.json")
    return SkeletonOutcome(
        run=run,
        workspace_kind=kind,
        run_file=run_file,
        artifacts=list(workspace.outputs()),
    )


__all__ = [
    "SkeletonOutcome",
    "default_generator",
    "build_workspace",
    "build_verifier",
    "run_general_task",
    "DEFAULT_OUTPUT_NAME",
]
