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

import inspect
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from kairos.skeleton.contracts import Task, Verdict
from kairos.skeleton.driver import SkeletonRun, run_task
from kairos.skeleton.verifiers import (
    CitationConsistencyVerifier,
    ProjectTestsVerifier,
)
from kairos.skeleton.workspaces import DocSetWorkspace, RepoWorkspace

logger = logging.getLogger(__name__)

#: How many prior chat turns (user + agent bubbles) a conversational turn may
#: replay into its prompt. Small on purpose: the *immediate* context is what a
#: follow-up ("重新分析一下…") needs, and the workspace context already spends the
#: bulk of the budget -- the history must not squeeze it out.
CHAT_HISTORY_MAX_MESSAGES = 20

#: Character budget for those replayed turns, independent of the count cap: 20
#: pasted walls of text would blow any window, so the newest turns are kept
#: until this many characters are used and the rest are dropped.
CHAT_HISTORY_MAX_CHARS = 4000

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


def _record_provider_call(*, model, provider, usage, duration_ms) -> None:
    """Best-effort: add one real-model call to the shared cost ledger.

    The loop's Gate Report reads ``kairos.cost`` (in-memory buffer + JSONL) for
    a run's spend. A skeleton call never appeared there, so the cost panel read
    0 calls / $0 even after a real run. Record the call with the *real* token
    counts the provider reported; the USD figure is left at 0.0 because no price
    is known for an arbitrary model on this path (litellm, the price table, is
    not installed here) and a fabricated figure is worse than an honest zero.
    Never raises -- a ledger failure must not fail a run.
    """
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    try:
        from kairos import cost as cost_mod
        cost_mod.record_entry(
            model=str(model or "unknown"),
            prompt_tokens=prompt,
            completion_tokens=completion,
            cost_usd=0.0,
            provider=str(provider or "unknown"),
            duration_ms=int(duration_ms or 0),
        )
    except Exception:  # noqa: BLE001 - the ledger must never break a call
        logger.debug("skeleton cost record failed", exc_info=True)


def default_generator() -> Optional[Callable]:
    """A real-model ``generate(prompt) -> str``, or ``None`` if none is set.

    Mirrors how the CLI resolves a provider for the skeleton
    (``kairos.cli._intake_llm`` / ``_skeleton_provider_generator``): the
    ``coder`` role's provider, wrapped as an async ``generate``. Never raises;
    a missing model degrades to ``None`` so the caller can report it instead of
    crashing a request. Each call is recorded in the shared cost ledger
    (``kairos.cost``) with the provider's real token usage.
    """
    try:
        from kairos import config as _pkg_config
        from kairos.llm.base import LLMMessage
        from kairos.llm.model_router import ModelRouter

        cfg = Path(_pkg_config.__file__).parent / "models_config.yaml"
        provider = ModelRouter(config_path=cfg.resolve()).get_provider_for_role("coder")
        if provider is None:
            return None

        # The provider only exposes the model on its config; capture it once so
        # the ledger entry carries a real model name.
        _cfg = getattr(provider, "config", None)
        _model = getattr(_cfg, "model", "") or ""
        _provider_name = getattr(_cfg, "provider", "") or ""

        async def _generate(prompt: str) -> str:
            started = time.time()
            response = await provider.complete([LLMMessage(role="user", content=prompt)])
            _record_provider_call(
                model=_model,
                provider=_provider_name,
                usage=getattr(response, "usage", None),
                duration_ms=int((time.time() - started) * 1000),
            )
            return getattr(response, "content", "") or ""

        return _generate
    except Exception as exc:  # noqa: BLE001 - a missing model must not raise
        logger.info("no model provider for the skeleton (%s)", exc)
        return None


def _provider_tool_client(provider):
    """Wrap a tool-aware provider as a ``complete(messages, tools=)`` client.

    The provider already speaks native function calling -- the Coder/Reviewer
    use it -- so this is a thin adapter that also records each call in the
    shared cost ledger, mirroring :func:`default_generator`.
    """
    _cfg = getattr(provider, "config", None)
    _model = getattr(_cfg, "model", "") or ""
    _provider_name = getattr(_cfg, "provider", "") or ""

    class _ToolClient:
        async def complete(self, messages, tools=None):
            started = time.time()
            response = await provider.complete(messages, tools=tools)
            _record_provider_call(
                model=_model,
                provider=_provider_name,
                usage=getattr(response, "usage", None),
                duration_ms=int((time.time() - started) * 1000),
            )
            return response

    return _ToolClient()


def default_tool_client():
    """A tool-capable model client for the general lane, or ``None``.

    This is the same seam :func:`default_generator` uses (the ``coder`` role's
    provider, which the Coder/Reviewer already drive native function calling
    with), except the returned client accepts tool schemas -- so the general
    lane can actually *open* the files it is told about.

    Returns ``None`` when no provider can run: none is configured, or the one
    that resolves has neither an api key nor a base url (a keyless config can
    only fail, so the caller keeps today's prompt-only path instead of dialing
    an endpoint that is guaranteed to reject it). Never raises.
    """
    try:
        from kairos import config as _pkg_config
        from kairos.llm.model_router import ModelRouter

        cfg = Path(_pkg_config.__file__).parent / "models_config.yaml"
        provider = ModelRouter(config_path=cfg.resolve()).get_provider_for_role("coder")
        if provider is None:
            return None
        _cfg = getattr(provider, "config", None)
        api_key = (getattr(_cfg, "api_key", "") or "").strip()
        base_url = (getattr(_cfg, "base_url", "") or "").strip()
        if not api_key and not base_url:
            return None
        return _provider_tool_client(provider)
    except Exception as exc:  # noqa: BLE001 - a missing tool client must not raise
        logger.info("no tool-capable model for the general skeleton (%s)", exc)
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


#: Prompt for a *conversational* general-lane turn (the chat path). Unlike the
#: deliverable prompt in ``PromptWorker`` this asks for a plain answer, because
#: a chat message ("你好", "这个项目是做什么的？") is not a document to be
#: produced -- and it must never make the worker write a file into the user's
#: workspace.
_CHAT_PROMPT = (
    "You are answering the user's message conversationally, inside their "
    "project workspace.\n\n"
    "Use the workspace context below when it is relevant to the message. If "
    "it is not relevant, just answer the question directly and briefly.\n\n"
    "WORKSPACE CONTEXT:\n{context}\n\n"
    "USER MESSAGE:\n{message}\n\n"
    "Write your reply now."
)


#: System prompt for the general lane when it is given the read-only file tools
#: (the usual case in production). Like ``_CHAT_PROMPT`` it asks for a plain
#: answer -- but it also tells the model it may *open* the files the user
#: attached, which is the whole point of giving this lane tools. The toolset is
#: read-only, so the prompt states that limit plainly: the model must not claim
#: to have written a file, run a command or reached the network.
_TOOL_CHAT_PROMPT = (
    "You are answering the user's message conversationally, inside their "
    "project workspace.\n\n"
    "You have read-only tools: use file_read to open a text file (or list a "
    "directory), doc_read for a .docx/.pptx, xlsx_read for a .xlsx, "
    "data_analyze for a .csv/.tsv, and grep/find to locate files. When the "
    "user attached a file (a [附件 / attachments] block names its path), open "
    "it before answering. The tools can only READ inside the project "
    "directory -- you cannot write files, run commands or reach the network, "
    "so never claim to have done any of those. When you have what you need, "
    "answer the question directly and briefly.\n\n"
    "WORKSPACE CONTEXT:\n{context}"
)


#: Header for the replayed conversation block. ``## Recent conversation`` marks
#: it unmistakably, and the body says out loud that these are *earlier turns*,
#: not a fresh instruction -- without that a follow-up-looking history entry
#: ("重新分析…") can be mistaken for the user's new command and re-executed.
_HISTORY_HEADER = (
    "## Recent conversation (earlier turns in this project)\n"
    "These are the previous exchanges with the user, provided so you keep the "
    "context of the conversation. They are background only -- NOT a new "
    "instruction: do not repeat, re-answer or re-execute them. Respond only "
    "to the new message."
)


def _default_history_db():
    """The live persistence to read chat history from, or ``None``.

    The chat lane is a leaf that the loop never enters, and this is the *one*
    place it reaches back for state. Resolved lazily (mirroring
    ``kairos.artifacts``) so importing this module never drags the API layer
    in, and best-effort: no orchestrator (a CLI run, a unit test) simply means
    no history, never an error.
    """
    try:
        from api.deps import orchestrator
        return getattr(orchestrator, "_db", None)
    except Exception:  # noqa: BLE001 - no API layer = no history, not a fault
        logger.debug("chat history: no orchestrator available", exc_info=True)
        return None


def _history_role(row: dict) -> str:
    """Map a stored chat row to ``"user"`` / ``"assistant"``.

    A user's own bubble is written with ``sender="user"`` / ``topic="user.chat"``
    by every entrance (web + IM); everything else on the chat channel (the
    Coder's reply, the skeleton's reply) is the agent talking.
    """
    sender = str(row.get("sender") or "").lower()
    topic = str(row.get("topic") or "")
    if sender.startswith("user") or topic == "user.chat":
        return "user"
    return "assistant"


def load_chat_history(
    project_id: Optional[str] = None,
    *,
    db: Any = None,
    current_message: str = "",
    limit: int = CHAT_HISTORY_MAX_MESSAGES,
    max_chars: int = CHAT_HISTORY_MAX_CHARS,
) -> list:
    """Best-effort: a project's recent conversation, oldest first.

    Reads the ``messages`` table (through ``Persistence.load_messages`` with
    ``chat_only=True``) scoped to ``project_id`` so one project's chat can
    never bleed into another's. Returns ``[{"role": ..., "content": ...}, ...]``
    in chronological order, capped by BOTH ``limit`` (newest N turns) and
    ``max_chars`` (newest turns that fit the character budget).

    ``current_message`` is the turn being answered *right now*. Every entrance
    persists the user's own bubble before it asks for the reply, so that bubble
    is already the newest row; passing it here lets the live turn be dropped
    **before** the cap is applied, so the ``limit`` counts real prior turns
    rather than the message we are already handling.

    Never raises: an unknown/absent project, a DB without the loader, a schema
    that predates ``messages``, or a bad row all degrade to ``[]`` -- a missing
    history must never turn a chat reply into an error.
    """
    if not project_id:
        return []
    database = db if db is not None else _default_history_db()
    loader = getattr(database, "load_messages", None)
    if not callable(loader):
        return []
    limit = int(limit)
    try:
        # One spare slot for the live turn we may drop just below.
        rows = loader(limit=limit + 1, project_id=project_id, chat_only=True)
    except Exception:  # noqa: BLE001 - history is context, never a hard dep
        logger.warning("chat history read failed for project %s",
                       project_id, exc_info=True)
        return []
    if not rows:
        return []

    current = (current_message or "").strip()
    remaining = int(max_chars)
    picked: list = []
    for row in rows:  # newest-first from the loader
        if not isinstance(row, dict):
            continue
        content = row.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        content = content.strip()
        if not content:
            continue
        role = _history_role(row)
        # The live turn is the newest row; never replay it as history.
        if not picked and role == "user" and current and content == current:
            continue
        if len(picked) >= limit:
            break                  # count cap reached
        if len(content) > remaining:
            if picked:
                break              # a newer turn already fits: stop here
            content = content[:max(0, remaining)].rstrip()
            if not content:
                break              # budget is zero: nothing to show
        picked.append({"role": role, "content": content})
        remaining -= len(content)
        if remaining <= 0:
            break
    picked.reverse()               # chronological order for the prompt
    return picked


def _render_history_block(message: str, history: Optional[Any]) -> str:
    """Render replayed turns into a labelled block; ``""`` when there are none.

    Returns ``""`` for no history, so the no-history prompt stays *byte for
    byte* what it was before history existed. A trailing entry equal to the
    current ``message`` is dropped: every entrance persists the user's own
    bubble *before* it asks for the reply, so the live turn is already the
    newest row and must not appear twice.
    """
    if not history:
        return ""
    items = [
        h for h in history
        if isinstance(h, dict) and str(h.get("content") or "").strip()
    ]
    current = (message or "").strip()
    if items:
        last = items[-1]
        if (str(last.get("role") or "").lower() == "user"
                and str(last.get("content") or "").strip() == current):
            items = items[:-1]
    if not items:
        return ""
    body = "\n".join(
        f'{"USER" if str(h.get("role") or "").lower() == "user" else "AGENT"}: '
        f'{h.get("content")}'
        for h in items
    )
    return f"{_HISTORY_HEADER}\n\n{body}"


def undecided_chat_verdict() -> Verdict:
    """The verdict a conversational general-lane turn carries: ``undecided``.

    A single chat answer is not a deliverable with pass/fail criteria, so the
    general lane runs its Worker **without a Verifier**. ``passed`` is ``None``
    (undecided) by design -- never ``False``, which would read as a failure.
    """
    return Verdict(
        passed=None,
        verifier="",
        reason="conversational turn: the general lane runs no verifier",
    )


async def run_chat_reply(
    *,
    kind: str,
    root: Any,
    message: str,
    generate: Optional[Callable] = None,
    max_context_chars: Optional[int] = None,
    tool_client: Any = None,
    project_id: Optional[str] = None,
    history: Optional[Any] = None,
) -> Optional[str]:
    """One conversational turn on the general lane; the reply text, or ``None``.

    The general lane's Worker (a model) is run WITHOUT a Verifier -- a chat
    answer has no pass/fail, so the verdict is :func:`undecided_chat_verdict`
    (``passed is None``) by design. This deliberately does **not** call
    ``PromptWorker.run``: that worker *emits an artifact* (writes a deliverable
    file) and demands citations, neither of which fits a chat turn and both of
    which would pollute the user's workspace on every "你好".

    Two model seams, tried in order:

    * **Read-only tools (preferred).** When a tool-capable client is available
      (``tool_client``, else :func:`default_tool_client`) the turn runs through
      :func:`kairos.skeleton.read_tools.run_read_tool_loop`: the model may open
      the files the user attached (the ``[附件]`` block) with the same
      sandboxed, capability-gated readers the Coder uses. This is what makes
      the general lane able to *read* an attachment instead of only being told
      its path.
    * **Prompt only (fallback).** Otherwise the historical seam
      (:func:`default_generator`, a ``generate(prompt) -> str``) is used -- the
      general lane degrades to a single text answer, exactly as before the
      tools existed.

    **Conversation history.** A conversational follow-up ("重新分析…") is
    meaningless without the turns before it, so prior chat is replayed in front
    of the prompt. Pass ``history`` (already-loaded rows from
    :func:`load_chat_history`) or just ``project_id`` and the history is read
    from the ``messages`` table here. Either way it is folded into the system
    prompt / prompt under a labelled ``## Recent conversation`` block; when
    there is none, both seams build the **exact** prompt they did before this
    parameter existed. Best-effort throughout: a missing table, no project, or
    an unreadable row leaves the single-turn reply untouched.

    Returns ``None`` when neither seam has a model (the caller surfaces that
    honestly instead of pretending a turn ran).
    """
    workspace = build_workspace(kind, root)

    if history is None and project_id:
        history = load_chat_history(project_id, current_message=message)
    history_block = _render_history_block(message, history)

    client = tool_client if tool_client is not None else default_tool_client()
    if client is not None:
        from kairos.skeleton.read_tools import read_only_tools, run_read_tool_loop

        tools = read_only_tools(workspace.root)
        context = workspace.as_prompt_context(
            max_chars=max_context_chars, query=message)
        system = _TOOL_CHAT_PROMPT.format(context=context)
        if history_block:
            # The loop's contract is ``system_prompt`` + ``message``: the
            # history is rendered *into* the system prompt rather than adding a
            # parameter, so ``run_read_tool_loop`` is untouched.
            system = f"{history_block}\n\n{system}"
        try:
            text = await run_read_tool_loop(
                client=client, tools=tools, system_prompt=system,
                message=message)
        except Exception:  # a provider failure must surface, not be swallowed
            logger.exception("general-lane tool loop failed")
            raise
        return "" if text is None else str(text)

    generator = generate if generate is not None else default_generator()
    if generator is None:
        return None
    context = workspace.as_prompt_context(
        max_chars=max_context_chars, query=message)
    prompt = _CHAT_PROMPT.format(context=context, message=message)
    if history_block:
        prompt = f"{history_block}\n\n{prompt}"
    try:
        text = generator(prompt)
        if inspect.isawaitable(text):
            text = await text
    except Exception:  # a provider failure must surface, not crash the route
        logger.exception("general-lane chat generation failed")
        raise
    return "" if text is None else str(text)


async def run_general_task(
    *,
    kind: str,
    root: Any,
    instruction: str,
    output_name: str = DEFAULT_OUTPUT_NAME,
    generate: Optional[Callable] = None,
    run_dir: Any = None,
    bus: Any = None,
    run_id: Optional[str] = None,
) -> SkeletonOutcome:
    """Run one task through the skeleton and verify it; return the outcome.

    ``run_id`` (optional) pins the run's identifier so a caller that must know
    it *before* the run finishes (the background route) can hand the same id
    back immediately and find the persisted record under it afterwards.

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

    run = await run_task(worker, workspace, task, verifier, bus=bus,
                         run_dir=run_dir, run_id=run_id)
    run_file = str(Path(run_dir) / f"skeleton-run-{run.run_id}.json")
    return SkeletonOutcome(
        run=run,
        workspace_kind=kind,
        run_file=run_file,
        artifacts=list(workspace.outputs()),
    )


__all__ = [
    "SkeletonOutcome",
    "CHAT_HISTORY_MAX_MESSAGES",
    "CHAT_HISTORY_MAX_CHARS",
    "default_generator",
    "default_tool_client",
    "build_workspace",
    "build_verifier",
    "load_chat_history",
    "run_general_task",
    "run_chat_reply",
    "undecided_chat_verdict",
    "DEFAULT_OUTPUT_NAME",
]
