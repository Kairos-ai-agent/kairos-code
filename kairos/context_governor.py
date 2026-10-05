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
