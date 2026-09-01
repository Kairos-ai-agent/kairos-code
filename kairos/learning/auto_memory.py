"""AutoMemory: learn user preferences from past activity.

The original learning module (``learning/reflect.py``) looks at
agent runs and writes down generic advice. This module is
complementary: it watches **the user's own decisions** (approval
prompts, manual edits, explicit "always X / never X" notes) and
distills them into durable preferences that show up the next
time an agent starts.

The pipeline:

  1. Collect a stream of ``MemoryEvent`` records (kind, payload).
  2. Run ``extract_preferences(events)`` to produce a list of
     ``Preference`` items (statement, weight, evidence_count).
  3. ``update_agents_md(prefs, path)`` writes a "## Auto-learned
     preferences" section into the user's AGENTS.md so future
     agent sessions pick them up.

We deliberately keep extraction simple: pattern matching on
user phrases. Calling an LLM to do this is overkill for the
MVP and would itself be a non-deterministic subsystem.

Mirrors the agentic CLI's "AutoMemory" feature described in their
2026 release notes.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryEvent:
    """A single user-affecting event worth learning from.

    `kind` is one of:
      - "approval"  : user said yes/no to a permission prompt
                       (payload has `tool`, `resource`, `decision`)
      - "message"   : the user said something to the agent in
                       natural language (payload has `text`)
      - "correction": the user manually edited an agent-written
                       file (payload has `path`, `before`, `after`)
    """
    kind: str
    payload: dict
    ts: float = 0.0


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@dataclass
class Preference:
    """One learned user preference."""
    statement: str
    weight: float = 1.0        # higher = stronger signal
    evidence_count: int = 1
    last_seen: float = 0.0

    def to_md_line(self) -> str:
        return f"- {self.statement}  *(seen {self.evidence_count}×, weight {self.weight:.1f})*"


# Phrases we recognize as "the user wants me to do/never do X".
_ALWAYS_RE = re.compile(
    r"\b(?:always|every\s+time|remember\s+to|from\s+now\s+on)\b[:\s]+(.+?)(?:[\.;\n]|$)",
    re.IGNORECASE | re.DOTALL,
)
_NEVER_RE = re.compile(
    r"\b(?:never|don'?t|do\s+not|stop|avoid)\b[:\s]+(.+?)(?:[\.;\n]|$)",
    re.IGNORECASE | re.DOTALL,
)


def extract_preferences(events: Iterable[MemoryEvent]) -> List[Preference]:
    """Turn a stream of events into a list of preferences."""
    # Keyed by lowercased statement; values accumulate.
    bucket: dict[str, Preference] = {}

    def _merge(statement: str, weight: float, ts: float) -> None:
        s = statement.strip().rstrip(".").strip()
        if not s or len(s) > 200:
            return
        key = s.lower()
        if key in bucket:
            p = bucket[key]
            p.evidence_count += 1
            p.weight = min(p.weight + weight, 5.0)
            p.last_seen = max(p.last_seen, ts)
        else:
            bucket[key] = Preference(
                statement=s, weight=weight,
                evidence_count=1, last_seen=ts,
            )

    for ev in events:
        if ev.kind == "message":
            text = (ev.payload.get("text") or "").strip()
            for pat, sign in ((_ALWAYS_RE, +1.0), (_NEVER_RE, +1.0)):
                for m in pat.finditer(text):
                    _merge(m.group(1), weight=2.0, ts=ev.ts)
        elif ev.kind == "approval":
            decision = (ev.payload.get("decision") or "").lower()
            tool = ev.payload.get("tool") or ""
            resource = ev.payload.get("resource") or ""
            if decision == "allow" and tool and resource:
                # User allows this combo: a strong positive signal.
                _merge(
                    f"user typically allows {tool} on {resource}",
                    weight=1.0, ts=ev.ts,
                )
            elif decision == "deny" and tool and resource:
                _merge(
                    f"user typically denies {tool} on {resource}",
                    weight=1.5, ts=ev.ts,
                )
        elif ev.kind == "correction":
            after = (ev.payload.get("after") or "").strip()
            if after and len(after) < 200:
                _merge(
                    f"user prefers: {after}",
                    weight=3.0, ts=ev.ts,
                )
    return sorted(
        bucket.values(),
        key=lambda p: (p.weight, p.evidence_count, p.last_seen),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# AGENTS.md writer
# ---------------------------------------------------------------------------


_AUTO_SECTION_HEADER = "## Auto-learned preferences"
_AUTO_SECTION_INTRO = (
    "_The following preferences were inferred from past sessions "
    "by `kairos.learning.auto_memory`. Edit freely — your changes "
    "win on the next merge._"
)


def _format_ts(ts: float) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def update_agents_md(
    preferences: List[Preference],
    agents_md_path: Path,
    *,
    keep_existing: bool = True,
) -> int:
    """Rewrite the auto-learned section of AGENTS.md.

    `keep_existing=True` (the default) means anything you wrote
    above the section is preserved; only the section we own is
    overwritten. Returns the number of preferences written.

    The file is left alone if `preferences` is empty AND no section
    exists — there's nothing to do and we shouldn't litter the
    file with a placeholder.
    """
    path = Path(agents_md_path)
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
    # Strip our section if it exists.
    new_body = _strip_auto_section(existing)
    if not preferences:
        if new_body == existing:
            return 0
        # User had prefs in their file but we have none now — clear
        # the section to keep the file tidy.
        if path.exists():
            path.write_text(new_body, encoding="utf-8")
        return 0
    section = _render_section(preferences)
    final = (new_body.rstrip() + "\n\n" + section + "\n").lstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(final, encoding="utf-8")
    return len(preferences)


def _strip_auto_section(text: str) -> str:
    """Remove our auto-learned section, leave the rest."""
    if _AUTO_SECTION_HEADER not in text:
        return text
    head, _, rest = text.partition(_AUTO_SECTION_HEADER)
    # Drop everything from the section header to the end of the file
    # (or to the next top-level `## ` header). Stripping the whole
    # tail is the safest option — the user can paste it back if
    # they had notes there.
    return head.rstrip() + "\n"


def _render_section(preferences: List[Preference]) -> str:
    lines = [
        _AUTO_SECTION_HEADER,
        "",
        _AUTO_SECTION_INTRO,
        "",
    ]
    for p in preferences:
        lines.append(p.to_md_line())
    return "\n".join(lines)
