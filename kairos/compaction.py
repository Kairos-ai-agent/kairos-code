"""Session compaction for long-running Coder/Reviewer loops.

As a loop runs, ``session.history`` accumulates every round's
review verdict + Coder summary. After ~10-20 rounds this can
exceed the model's context window. This module summarizes the
older rounds into a single "compaction" record and writes the
result back to ``session.history`` as a synthetic round, so
the model sees a stable, bounded context on subsequent rounds.

The compaction strategy is intentionally simple — we don't ask
the LLM to summarize (which would add latency + cost), we just
keep the most recent N rounds verbatim and squish the older
ones into a single digest record with score trend + key issues.
The agent prompt can opt to read this digest instead of the
full history.

Triggers:
  - :func:`maybe_compact` is called once per round; it
    compacts only when the history exceeds ``threshold_rounds``
    (default 12).
  - The compaction is a no-op when the history is short.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CompactedDigest:
    """A single record that summarizes older rounds."""
    type: str = "compaction"  # discriminator in history
    rounds: int = 0  # how many original rounds were folded
    rounds_covered: List[int] = field(default_factory=list)
    score_min: float = 0.0
    score_max: float = 0.0
    score_avg: float = 0.0
    score_trend: List[float] = field(default_factory=list)
    issues_signature: List[str] = field(default_factory=list)
    last_approve: bool = False
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CompactedDigest":
        d = dict(d)
        d.setdefault("type", "compaction")
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


DEFAULT_THRESHOLD_ROUNDS = 12
DEFAULT_KEEP_RECENT = 5  # how many recent rounds to keep verbatim


def _score_of(entry: Dict[str, Any]) -> float:
    rev = entry.get("review") if isinstance(entry, dict) else None
    if not isinstance(rev, dict):
        return 0.0
    try:
        return float(rev.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _issues_of(entry: Dict[str, Any]) -> List[str]:
    rev = entry.get("review") if isinstance(entry, dict) else None
    if not isinstance(rev, dict):
        return []
    issues = rev.get("issues") or []
    if not isinstance(issues, list):
        return []
    sigs = []
    for it in issues[:5]:  # top 5 per round
        if isinstance(it, dict):
            cat = it.get("category", "")
            sev = it.get("severity", "")
            sigs.append(f"{sev}:{cat}")
        else:
            sigs.append(str(it)[:80])
    return sigs


def build_digest(rounds_to_compact: List[Dict[str, Any]]) -> CompactedDigest:
    """Fold *rounds_to_compact* into a single :class:`CompactedDigest`.

    Caller is responsible for choosing which rounds to fold (typically
    ``session.history[:-DEFAULT_KEEP_RECENT]``).
    """
    if not rounds_to_compact:
        return CompactedDigest(summary="(empty)")

    scores = [_score_of(e) for e in rounds_to_compact]
    n = len(scores)
    approve_count = sum(
        1 for e in rounds_to_compact
        if isinstance(e.get("review"), dict) and e["review"].get("approve")
    )

    # Issue signature: count occurrences across the folded rounds
    sig_counter: Dict[str, int] = {}
    for e in rounds_to_compact:
        for sig in _issues_of(e):
            sig_counter[sig] = sig_counter.get(sig, 0) + 1
    top_sigs = sorted(sig_counter.items(), key=lambda x: -x[1])[:5]
    top_sig_list = [f"{sig}({n}x)" for sig, n in top_sigs]

    avg = sum(scores) / n if n else 0.0
    summary = (
        f"Compacted {n} round(s): scores {min(scores):.0f}-{max(scores):.0f} "
        f"(avg {avg:.0f}), {approve_count} approved, "
        f"top issues: {', '.join(top_sig_list) or 'none'}"
    )
    return CompactedDigest(
        rounds=n,
        rounds_covered=[int(e.get("round", 0)) for e in rounds_to_compact],
        score_min=min(scores) if scores else 0.0,
        score_max=max(scores) if scores else 0.0,
        score_avg=avg,
        score_trend=scores,
        issues_signature=top_sig_list,
        last_approve=bool(rounds_to_compact[-1].get("review", {}).get("approve", False))
            if rounds_to_compact else False,
        summary=summary,
    )


def maybe_compact(
    history: List[Dict[str, Any]],
    *,
    threshold: int = DEFAULT_THRESHOLD_ROUNDS,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> List[Dict[str, Any]]:
    """Return a compacted copy of *history* if it exceeds *threshold*
    rounds, else return it unchanged.

    The compaction replaces the oldest ``len(history) - keep_recent``
    entries with a single :class:`CompactedDigest` (as a dict in
    the same shape as a real round entry, but with ``type ==
    "compaction"``).

    Edge cases:
      - ``keep_recent <= 0``  → fold everything (single digest)
      - ``keep_recent >= len(history)`` → nothing to fold; return as-is
      - ``len(history) < threshold`` → no compaction; return as-is

    Always returns a new list — never mutates the input.
    """
    if not history or len(history) < threshold:
        return list(history)
    if keep_recent >= len(history):
        return list(history)

    if keep_recent <= 0:
        to_compact = list(history)
        to_keep: list = []
    else:
        to_compact = history[:-keep_recent]
        to_keep = history[-keep_recent:]

    digest = build_digest(to_compact)
    return [digest.to_dict()] + list(to_keep)


def compaction_stats(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Inspect what *maybe_compact* would do without actually
    compacting. Useful for /status displays."""
    n = len(history)
    would_compact = n >= DEFAULT_THRESHOLD_ROUNDS
    folded = max(0, n - DEFAULT_KEEP_RECENT) if would_compact else 0
    return {
        "history_rounds": n,
        "would_compact": would_compact,
        "rounds_folded": folded,
        "rounds_kept": n - folded,
    }
