"""Context governance for long sessions: elide, then shrink, never die.

Two jobs, both about the same scarce resource — the attention budget of a
fixed-size context window:

* :func:`elide_old_tool_results` — the lightest-touch compaction there is.
  A tool result that scrolled out of the recent window carries almost no
  signal (``read`` output from twenty turns ago, a directory listing the
  agent has since summarised in its own words) but still costs its full
  token count on *every* subsequent request. Replacing the body with a
  one-line stub keeps the message — and therefore the reply/pairing
  invariant that providers enforce — while giving the budget back.

  This runs at request-assembly time and is **pure**: the stored session
  keeps the full text, so nothing is lost for good and re-running the tool
  is always possible. That is also why it is safe to do on every turn
  instead of only when things are already on fire.

* :func:`shrink_for_overflow` — the plan for when the provider has already
  said no. ``prompt is too long`` / ``context_length_exceeded`` / HTTP 413
  are **not** failures to surface to the user; they are instructions to
  compress and retry. This returns a strictly smaller message list built
  in escalating steps (elide tool bodies → drop the oldest turns), keeping
  the system messages and the message that is actually being answered.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Tell the model what happened and what to do about it. A silent stub reads
# like a tool that returned nothing, and the agent wastes a turn re-running
# something it does not need; an explicit note keeps it moving.
ELIDED_PLACEHOLDER = (
    "[{tool} output elided to save context — {chars} chars removed. "
    "Re-run the tool if you still need it.]"
)

#: Injected when a request had to be trimmed to fit a prompt budget. It goes in
#: as a system message so the model KNOWS the transcript in front of it is
#: incomplete — trimming silently would leave it asserting stale conclusions as
#: if the whole history were present.
CONTEXT_TRIM_NOTICE = (
    "[context trimmed to fit this model's {budget}-token prompt budget: "
    "{elided} tool result(s) elided, {tools_dropped} tool schema(s) removed, "
    "{dropped} older message(s) dropped{system_note}. "
    "The full conversation is NOT in this request — if a conclusion depends on "
    "detail you no longer see, say so instead of guessing.]"
)

#: Tool results newer than this many are never touched.
DEFAULT_KEEP_RECENT_TOOL_RESULTS = 4

# Consecutive failed summary attempts after which the agent stops asking. A
# transcript the provider rejects will be rejected identically next round, and
# every attempt costs a whole LLM call (up to the per-call timeout), so past
# this point the retention window does the bounding instead.
MAX_SUMMARIZE_FAILURES = 3

#: Bodies shorter than this are left alone — a stub would save nothing and
#: only make the transcript harder to read.
DEFAULT_MIN_ELIDE_CHARS = 240

#: Under overflow pressure, only this many tool results survive intact.
OVERFLOW_KEEP_RECENT_TOOL_RESULTS = 1

#: Under overflow pressure, this many trailing messages stay verbatim.
OVERFLOW_KEEP_RECENT_MESSAGES = 8


@dataclass
class ElisionReport:
    """What an elision/shrink pass actually did."""

    elided: int = 0
    dropped: int = 0
    chars_saved: int = 0

    def __bool__(self) -> bool:
        return bool(self.elided or self.dropped)

    def summary(self) -> str:
        bits = []
        if self.elided:
            bits.append(f"{self.elided} tool result(s) elided")
        if self.dropped:
            bits.append(f"{self.dropped} message(s) dropped")
        if self.chars_saved:
            bits.append(f"~{self.chars_saved // 4} tokens freed")
        return ", ".join(bits) or "nothing to do"


def _with_content(message: Any, content: str) -> Any:
    """Return a copy of *message* carrying *content*.

    Works with the pydantic ``LLMMessage`` and with the plain dicts used by
    the ``/compact`` route, so this module stays ignorant of both.
    """
    if isinstance(message, dict):
        clone = dict(message)
        clone["content"] = content
        return clone
    try:
        clone = message.model_copy(deep=True)          # pydantic v2
        clone.content = content
        return clone
    except Exception:                                   # noqa: BLE001
        pass
    try:
        clone = message.copy()
        clone.content = content
        return clone
    except Exception:                                   # noqa: BLE001
        return message


def _role_of(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "role", "") or "")


def _content_of(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _name_of(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("name") or "tool")
    return str(getattr(message, "name", "") or "tool")


def elide_old_tool_results(
    messages: Sequence[Any],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT_TOOL_RESULTS,
    min_chars: int = DEFAULT_MIN_ELIDE_CHARS,
) -> tuple[list[Any], ElisionReport]:
    """Stub out old tool bodies, keeping the most recent *keep_recent* intact.

    Returns ``(new_messages, report)``. ``messages`` is never mutated, and
    non-tool messages are passed through untouched — including the
    ``tool_call_id`` of each elided message, so the request stays valid.
    """
    out: list[Any] = list(messages)
    report = ElisionReport()

    positions = [i for i, m in enumerate(out) if _role_of(m) == "tool"]
    if keep_recent > 0:
        targets = positions[:-keep_recent]
    else:
        targets = positions

    for i in targets:
        original = out[i]
        body = _content_of(original)
        if len(body) < min_chars:
            continue
        stub = ELIDED_PLACEHOLDER.format(
            tool=_name_of(original), chars=len(body),
        )
        out[i] = _with_content(original, stub)
        report.elided += 1
        report.chars_saved += len(body) - len(stub)

    return out, report


def shrink_for_overflow(
    messages: Sequence[Any],
    *,
    keep_recent_messages: int = OVERFLOW_KEEP_RECENT_MESSAGES,
    keep_recent_tool_results: int = OVERFLOW_KEEP_RECENT_TOOL_RESULTS,
) -> tuple[list[Any], ElisionReport]:
    """Build a strictly smaller request after the provider rejected the last one.

    Escalation, cheapest first:

    1. elide every tool body except the newest one — usually enough, and it
       costs the model nothing it was still using;
    2. drop the oldest non-system messages, keeping the system prompts (role,
       skills, running summary), the last *keep_recent_messages* turns, and
       always the final message, which is the one being answered.

    The result may break tool-call/reply pairing; ``_sanitize_memory`` runs
    afterwards and repairs that, which is why dropping whole messages here is
    acceptable.
    """
    shrunk, report = elide_old_tool_results(
        messages, keep_recent=keep_recent_tool_results,
    )

    if keep_recent_messages > 0 and len(shrunk) > keep_recent_messages:
        head = [m for m in shrunk if _role_of(m) == "system"]
        tail = shrunk[len(shrunk) - keep_recent_messages:]
        # The system prompts must stay first; a tail that already contains a
        # system message would duplicate it, so filter those out of the tail.
        tail = [m for m in tail if _role_of(m) != "system"]
        const: list[Any] = list(head) + tail
        # The final message is the one under discussion — never lose it.
        if shrunk and (not const or const[-1] is not shrunk[-1]):
            const.append(shrunk[-1])
        report.dropped = len(shrunk) - len(const)
        report.chars_saved += sum(
            len(_content_of(m)) for m in shrunk
        ) - sum(len(_content_of(m)) for m in const)
        shrunk = const

    return shrunk, report


def context_stats(messages: Sequence[Any]) -> dict[str, Any]:
    """Cheap introspection for /status and tests (chars/4 ≈ tokens)."""
    total_chars = sum(len(_content_of(m)) for m in messages)
    tool_chars = sum(
        len(_content_of(m)) for m in messages if _role_of(m) == "tool"
    )
    return {
        "messages": len(messages),
        "chars": total_chars,
        "approx_tokens": total_chars // 4,
        "tool_messages": sum(1 for m in messages if _role_of(m) == "tool"),
        "tool_chars": tool_chars,
    }


# ===========================================================================
# Prompt-budget fitting: keep a request under a model's window BEFORE sending.
#
# ``shrink_for_overflow`` above reacts to a provider that already said no. This
# is the pre-emptive half, and it exists for local models: LM Studio behind an
# 8192-token window rejects the whole call the moment the assembled prompt
# (system + history + the full tool catalogue) crosses it — the real failure was
# ``request (8208 tokens) exceeds the available context size (8192 tokens)``.
# Nothing here fires unless a budget is supplied, so a cloud model with no
# known window is untouched.
# ===========================================================================

#: When fitting a budget, keep at most this many trailing messages verbatim.
BUDGET_KEEP_RECENT_MESSAGES = OVERFLOW_KEEP_RECENT_MESSAGES

#: The system prompt is the LAST thing trimmed (its instructions matter most),
#: and even then at least this many characters survive.
BUDGET_MIN_SYSTEM_CHARS = 2000

#: Marker appended to a system prompt that had to be cut.
SYSTEM_TRIM_MARKER = "\n\n[… earlier system instructions truncated to fit the model window …]"


@dataclass
class BudgetReport:
    """What a :func:`fit_to_budget` pass actually changed."""

    budget_tokens: int = 0
    approx_tokens_before: int = 0
    approx_tokens_after: int = 0
    elided: int = 0          # tool bodies stubbed out
    tools_dropped: int = 0   # tool schemas removed
    dropped: int = 0         # whole messages removed
    system_trimmed: bool = False

    @property
    def trimmed(self) -> bool:
        return bool(self.elided or self.tools_dropped or self.dropped
                    or self.system_trimmed)

    def summary(self) -> str:
        bits = []
        if self.elided:
            bits.append(f"{self.elided} tool result(s) elided")
        if self.tools_dropped:
            bits.append(f"{self.tools_dropped} tool schema(s) removed")
        if self.dropped:
            bits.append(f"{self.dropped} message(s) dropped")
        if self.system_trimmed:
            bits.append("system prompt truncated")
        return ", ".join(bits) or "nothing to do"


def approx_prompt_tokens(messages: Sequence[Any],
                         tools: Sequence[Any] | None = None) -> int:
    """Rough token count of a whole request (chars/4, the agent's own heuristic).

    Counts message contents AND the tool schemas serialised the way they are
    sent: the schemas are the block that made a 22-tool request overshoot a
    small window, so a budget that ignored them would not have prevented it.
    """
    chars = sum(len(_content_of(m)) for m in messages)
    for tool in tools or []:
        try:
            chars += len(json.dumps(tool, ensure_ascii=False))
        except (TypeError, ValueError):
            chars += len(str(tool))
    return chars // 4


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _over_budget(messages: Sequence[Any], tools: Sequence[Any] | None,
                 budget: int) -> bool:
    return approx_prompt_tokens(messages, tools) > budget


def _drop_oldest(messages: Sequence[Any], keep_recent: int) -> tuple[list[Any], int]:
    """Drop the oldest messages, never the system prompts, the newest tail, or
    the message currently being answered (the last message + last user turn)."""
    n = len(messages)
    if keep_recent <= 0 or n <= keep_recent:
        return list(messages), 0
    keep_idx = set(range(n - keep_recent, n))
    for i, m in enumerate(messages):
        if _role_of(m) == "system":
            keep_idx.add(i)          # role + skills + summary always survive
    keep_idx.add(n - 1)              # the message under discussion
    for i in range(n - 1, -1, -1):   # the user's current message
        if _role_of(messages[i]) == "user":
            keep_idx.add(i)
            break
    kept = [m for i, m in enumerate(messages) if i in keep_idx]
    return kept, n - len(kept)


def _trim_system(messages: Sequence[Any],
                 min_chars: int = BUDGET_MIN_SYSTEM_CHARS) -> tuple[list[Any], bool]:
    """Cut the first system prompt in half (floor ``min_chars``); last resort."""
    for i, m in enumerate(messages):
        if _role_of(m) != "system":
            continue
        body = _content_of(m)
        if len(body) <= min_chars:
            return list(messages), False
        out = list(messages)
        out[i] = _with_content(m, body[:max(min_chars, len(body) // 2)] + SYSTEM_TRIM_MARKER)
        return out, True
    return list(messages), False


def _insert_trim_notice(messages: Sequence[Any], report: BudgetReport) -> list[Any]:
    """Put the trim notice right after the first system message (or at the top)."""
    note = CONTEXT_TRIM_NOTICE.format(
        budget=report.budget_tokens,
        elided=report.elided,
        tools_dropped=report.tools_dropped,
        dropped=report.dropped,
        system_note=", system prompt truncated" if report.system_trimmed else "",
    )
    out = list(messages)
    notice_msg: Any
    if out and _role_of(out[0]) == "system":
        # Clone the shape of an existing message so this works for both the
        # pydantic LLMMessage and the plain dicts the /compact route uses.
        notice_msg = _with_content(out[0], note)
        out.insert(1, notice_msg)
    else:
        out.insert(0, {"role": "system", "content": note})
    return out


def fit_to_budget(
    messages: Sequence[Any],
    tools: Sequence[Any] | None = None,
    budget_tokens: int | None = None,
    *,
    core_tool_names: Sequence[str] | None = None,
    keep_recent_messages: int = BUDGET_KEEP_RECENT_MESSAGES,
) -> tuple[list[Any], Any, BudgetReport]:
    """Trim a request to fit ``budget_tokens``, cheapest loss first.

    Escalation, in the order the model can best afford:

    1. **elide old tool bodies** — the existing elision mechanism, keeps every
       message and only stubs the parts that have scrolled out of use;
    2. **reduce the tool catalogue** to ``core_tool_names`` — the schemas are a
       fixed overhead on every request (see ``kairos.tools.light_mode``);
    3. **drop the oldest messages** — never the system prompts, the newest
       tail, the last user turn, or the message being answered;
    4. **trim the system prompt** — the last resort, and only to a floor.

    ``budget_tokens`` of ``None`` / 0 / negative returns the inputs unchanged
    (the historical behaviour). Whenever anything was cut, a ``system`` notice
    is inserted so the model is told the transcript is incomplete — a silent
    trim would let it treat a partial history as the whole one.

    Returns ``(messages, tools, report)``. ``tools`` is returned in the same
    shape it was given (list or None). Pairing broken by step 3 must be
    repaired by the caller with ``_sanitize_memory``.
    """
    had_tools = tools is not None
    msgs = list(messages)
    tool_list: list[Any] = list(tools) if tools else []
    report = BudgetReport(budget_tokens=int(budget_tokens or 0))
    if not budget_tokens or budget_tokens <= 0:
        return msgs, (tool_list if had_tools else tools), report

    report.approx_tokens_before = approx_prompt_tokens(msgs, tool_list)
    if report.approx_tokens_before <= budget_tokens:
        report.approx_tokens_after = report.approx_tokens_before
        return msgs, (tool_list if had_tools else tools), report

    # 1) elide tool bodies (never drops a message).
    if _over_budget(msgs, tool_list, budget_tokens):
        msgs, elision = elide_old_tool_results(
            msgs, keep_recent=OVERFLOW_KEEP_RECENT_TOOL_RESULTS,
        )
        report.elided += elision.elided

    # 2) reduce the tool catalogue to the core set.
    if (core_tool_names is not None and tool_list
            and _over_budget(msgs, tool_list, budget_tokens)):
        core = set(core_tool_names)
        kept = [t for t in tool_list if _tool_name(t) in core]
        report.tools_dropped = len(tool_list) - len(kept)
        tool_list = kept

    # 3) drop the oldest messages.
    if _over_budget(msgs, tool_list, budget_tokens):
        msgs, dropped = _drop_oldest(msgs, keep_recent_messages)
        report.dropped = dropped

    # 4) trim the system prompt.
    if _over_budget(msgs, tool_list, budget_tokens):
        msgs, report.system_trimmed = _trim_system(msgs)

    if report.trimmed:
        msgs = _insert_trim_notice(msgs, report)

    report.approx_tokens_after = approx_prompt_tokens(msgs, tool_list)
    return msgs, (tool_list if had_tools else tools), report
