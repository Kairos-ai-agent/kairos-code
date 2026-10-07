"""Domain-neutral agent skeleton: Workspace / Worker / Verifier.

Lifts the code loop's three hidden assumptions into pluggable interfaces so
non-code tasks (read docs -> produce a report, etc.) run on the same shape as
the existing Coder -> Reviewer loop. The Coder and Reviewer are wrapped as one
implementation each (``adapters``); they are not rewritten.
"""
from __future__ import annotations

from kairos.skeleton.adapters import (
    APPROVE_SCORE_THRESHOLD,
    CoderWorker,
    PromptWorker,
    ReviewerVerifier,
    verdict_from_review,
)
from kairos.skeleton.contracts import (
    Task,
    Verdict,
    Verifier,
    Worker,
    WorkerResult,
    Workspace,
)
from kairos.skeleton.driver import SkeletonRun, run_once, run_task
from kairos.skeleton.verifiers import (
    AssertionVerifier,
    HumanVerifier,
    ProjectTestsVerifier,
    RubricVerifier,
    ToolOracleVerifier,
    VerifierRegistry,
    build_default_registry,
)
from kairos.skeleton.workspaces import DocSetWorkspace, FileWorkspace, RepoWorkspace

__all__ = [
    # contracts
    "Task", "WorkerResult", "Verdict",
    "Workspace", "Worker", "Verifier",
    # workspaces
    "FileWorkspace", "RepoWorkspace", "DocSetWorkspace",
    # verifiers
    "VerifierRegistry", "build_default_registry",
    "ProjectTestsVerifier", "AssertionVerifier", "RubricVerifier",
    "HumanVerifier", "ToolOracleVerifier",
    # adapters
    "CoderWorker", "ReviewerVerifier", "PromptWorker",
    "verdict_from_review", "APPROVE_SCORE_THRESHOLD",
    # driver
    "SkeletonRun", "run_once", "run_task",
]
