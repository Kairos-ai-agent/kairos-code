"""Domain-neutral skeleton interfaces: Workspace / Worker / Verifier.

Why this exists
---------------
Kairos's main loop is a *code* loop: ``Coder`` edits a repo, ``Reviewer``
scores the diff, gates approve on a threshold. That loop is useful but it is
not the whole agent -- nothing about "read three documents and produce a
comparison report" fits the "edit a repo + run its tests" shape.

This module lifts the loop's three hidden assumptions into explicit,
pluggable interfaces:

* ``Workspace`` -- *what* we work on (a git repo, a pile of docs, ...).
* ``Worker``    -- *who* does the work (the Coder is one implementation).
* ``Verifier``  -- *how* we decide it worked (tests / assertion / rubric /
  human / tool-oracle; the Reviewer is one implementation).

The existing Coder/Reviewer are **not** rewritten: ``kairos/skeleton/adapters.py``
wraps them so they become one implementation of these interfaces, exactly as
the gap report (``GENERAL_AGENT_GAP_REPORT_2026-10-07.md`` §P0-1) proposed.

Design rule: a verdict is *structured data*. "Did the criteria hold" is a
boolean per criterion in ``Verdict.evidence``, never an LLM score we have to
trust. Scores, when present, are an add-on, not the decision.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Hard ceiling on how many characters of workspace *resource bodies* a worker
#: may inline into a single prompt. Mirrors ``agents.base.MAX_PUBLISHED_TEXT``
#: (the same "never silently drop the tail" idea): once the ceiling is hit the
#: remainder is cut and the cut is stated in-band, never made invisible.
MAX_WORKSPACE_CONTEXT_CHARS = 200_000


@dataclass
class Task:
    """A domain-neutral unit of work.

    ``instruction`` is free-form (prose, a ticket, a task record). ``inputs``
    names the resources inside the workspace this task cares about; when it is
    ``None`` the workspace's own ``resources()`` is used. ``output_name`` is
    the artifact the worker is expected to produce.
    """

    instruction: str
    output_name: str = "output.txt"
    inputs: Optional[List[str]] = None
    title: str = ""
    id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:8]
        if not self.title:
            first = (self.instruction or "").strip().splitlines()
            self.title = (first[0][:80] if first else "task") or "task"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "instruction": self.instruction,
            "output_name": self.output_name,
            "inputs": list(self.inputs) if self.inputs is not None else None,
            "title": self.title,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        data = data or {}
        inputs = data.get("inputs")
        return cls(
            instruction=data.get("instruction", ""),
            output_name=data.get("output_name", "output.txt"),
            inputs=list(inputs) if inputs is not None else None,
            title=data.get("title", ""),
            id=data.get("id", ""),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class WorkerResult:
    """What a :class:`Worker` produced.

    ``output`` is the worker's primary deliverable (for an LLM worker, its
    final text; for a file-editing coder, its round summary). ``artifacts``
    lists workspace refs the worker created.
    """

    ok: bool
    output: str = ""
    artifacts: List[str] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "artifacts": list(self.artifacts),
            "summary": self.summary,
            "error": self.error,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkerResult":
        data = data or {}
        return cls(
            ok=bool(data.get("ok", False)),
            output=data.get("output", ""),
            artifacts=list(data.get("artifacts") or []),
            summary=data.get("summary", ""),
            error=data.get("error"),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class Verdict:
    """A structured verification result.

    ``passed`` is ``True`` (criteria satisfied), ``False`` (not), or ``None``
    (undecided -- e.g. a human approval that has not arrived yet). Every
    criterion the verifier checked lives in ``evidence`` as
    ``{"criterion": str, "satisfied": bool | None, ...}`` so the decision is
    inspectable data rather than a hidden model score.

    ``requires_human`` marks a verdict that is intentionally blocked on a
    human (see the ``human`` verifier) rather than a failure.
    """

    passed: Optional[bool]
    reason: str = ""
    score: Optional[float] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    verifier: str = ""
    requires_human: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "score": self.score,
            "evidence": [dict(e) for e in self.evidence],
            "verifier": self.verifier,
            "requires_human": self.requires_human,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Verdict":
        data = data or {}
        return cls(
            passed=data.get("passed"),
            reason=data.get("reason", ""),
            score=data.get("score"),
            evidence=[dict(e) for e in (data.get("evidence") or [])],
            verifier=data.get("verifier", ""),
            requires_human=bool(data.get("requires_human", False)),
            meta=dict(data.get("meta") or {}),
        )


class Workspace(ABC):
    """*What* a worker acts on: a repo, a document set, an external handle.

    Implementations must be honest about what they support via
    ``capabilities`` so a verifier can decline gracefully (e.g. the ``tests``
    verifier returns ``passed=None`` on a workspace that has no tests).
    """

    kind: str = "abstract"
    capabilities: frozenset = frozenset()

    @abstractmethod
    def resources(self) -> List[str]:
        """Names of the inputs available in this workspace."""

    @abstractmethod
    def read(self, ref: str) -> str:
        """Read one input resource by the name ``resources()`` returned."""

    @abstractmethod
    def emit(self, name: str, content: str) -> str:
        """Write a produced artifact; return the ref other code can read back."""

    def read_output(self, name: str) -> Optional[str]:
        """Read a previously emitted artifact, or ``None`` if absent."""
        return None

    def outputs(self) -> List[str]:
        """Refs of every artifact emitted so far (best effort)."""
        return []

    def as_context(self, refs: Optional[List[str]] = None) -> str:
        """Render inputs as prompt-ready text with per-resource headers."""
        chosen = list(refs) if refs is not None else self.resources()
        chunks: List[str] = []
        for ref in chosen:
            try:
                body = self.read(ref)
            except Exception as exc:  # never let a bad resource break the run
                body = f"(unreadable: {exc})"
            chunks.append(f"INPUT: {ref}\n{body}")
        return "\n\n".join(chunks)

    def as_prompt_context(
        self, refs: Optional[List[str]] = None, *, max_chars: Optional[int] = None,
        query: Optional[str] = None,
    ) -> str:
        """Render inputs **with their bodies**, bounded and honest.

        This is the rendering a *worker* feeds to a model -- unlike
        :meth:`as_context`, which a workspace may override to emit a cheap
        listing (``RepoWorkspace`` does exactly that, because inlining a whole
        repository is useless). Every named resource's *content* is inlined
        under a total ``max_chars`` ceiling (``None`` -> the module default),
        and the failure modes are stated instead of hidden:

        * a resource that cannot be read renders as ``(unreadable: ...)`` --
          never silently dropped;
        * a workspace with no resources renders an explicit note rather than
          the empty string, so a model cannot be handed a blank context and
          then blamed for saying "no input was provided";
        * when the ceiling is reached, the tail is cut and a ``[TRUNCATED]``
          note names every input that was cut short or omitted entirely.

        ``query`` (optional) is the task text. When the workspace's combined
        body size exceeds the ceiling **and** a query is given, the ceiling is
        spent on the inputs that actually match the task (ranked excerpts via
        :func:`kairos.memory.doc_search.render_relevant_excerpts`, each naming
        its source file) instead of on whatever ``resources()`` happened to
        list first. A small workspace, or one where nothing matches the query,
        renders exactly as it always did -- this only changes what a *large*
        workspace hands the model.
        """
        cap = MAX_WORKSPACE_CONTEXT_CHARS if max_chars is None else int(max_chars)
        chosen = list(refs) if refs is not None else self.resources()
        if not chosen:
            return ("(workspace exposes no readable inputs: resources() returned "
                    "an empty list)")

        # Read each body once; keep unreadable inputs as an explicit string.
        read: List[tuple] = []  # (ref, body_or_None, error_or_None)
        total = 0
        for ref in chosen:
            try:
                body = self.read(ref)
            except Exception as exc:  # honest, never silent
                read.append((ref, None, str(exc)))
                continue
            body = "" if body is None else str(body)
            total += len(body)
            read.append((ref, body, None))

        # Large workspace + a task to steer by -> spend the ceiling on relevant
        # excerpts instead of on the first inputs in list order.
        if query and str(query).strip() and total > cap:
            docs = [{"file": ref, "body": body}
                    for ref, body, err in read if err is None]
            try:
                from kairos.memory.doc_search import render_relevant_excerpts
                text, _meta = render_relevant_excerpts(query, docs, cap=cap)
            except Exception:  # retrieval must never break a run
                text = None
            if text:
                # Never hide an unreadable input: fold those in honestly.
                unreadable = [ref for ref, _b, err in read if err is not None]
                if unreadable:
                    text += ("\n\n(unreadable inputs: " + ", ".join(unreadable) + ")")
                return text

        # Small workspace (or nothing matched): the historical bounded render.
        blocks: List[str] = []
        used = 0
        truncated: List[str] = []
        for ref, body, err in read:
            if err is not None:
                blocks.append(f"INPUT: {ref}\n(unreadable: {err})")
                continue
            remaining = cap - used
            if remaining <= 0:
                truncated.append(ref)
                continue
            if len(body) > remaining:
                body = body[:remaining]
                truncated.append(ref)
            used += len(body)
            blocks.append(f"INPUT: {ref}\n{body}")
        text = "\n\n".join(blocks)
        if truncated:
            text += (
                f"\n\n[TRUNCATED] workspace context hit the {cap}-char cap; these "
                "inputs were cut short or omitted entirely: " + ", ".join(truncated)
            )
        return text

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


class Worker(ABC):
    """A domain-neutral executor. The existing Coder is one implementation."""

    name: str = "worker"

    @abstractmethod
    async def run(self, workspace: Workspace, task: Task) -> WorkerResult:
        """Do the task against the workspace and return the deliverable."""


class Verifier(ABC):
    """A domain-neutral check. The existing Reviewer is one implementation."""

    name: str = "verifier"

    @property
    def ready(self) -> bool:
        """Configured enough to be consulted. Defaults to True.

        A verifier that has nothing to check against (an ``assertion`` with no
        check, a ``tool_oracle`` with no oracle) overrides this to ``False`` so
        callers can tell "registered" from "usable".
        """
        return True

    @property
    def decides(self) -> bool:
        """Whether this verifier can ever return ``passed`` True/False.

        False for gates like :class:`~kairos.skeleton.verifiers.HumanVerifier`
        that only ever block for a human.
        """
        return True

    @abstractmethod
    async def verify(
        self, workspace: Workspace, task: Task, result: WorkerResult
    ) -> Verdict:
        """Decide whether ``result`` satisfies the task, with evidence."""
