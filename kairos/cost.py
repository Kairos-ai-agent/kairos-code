"""Token + USD cost tracking for Kairos.

Two responsibilities:

  1. **Pricing catalog** — known $/1M-token rates for major models.
     The orchestrator records usage against a model name and the
     tracker converts tokens → USD.
  2. **Per-project / per-agent aggregation** — usage rolls up
     by project, by agent role, by model, and by day. The
     aggregations live in memory; the orchestrator is free to
     also persist them via the existing persistence layer.

The tracker is independent of the LLM provider: any code path
that knows the model + token counts can call
:func:`record_usage`. The reflection module, the bench runner,
the loop runner, and the metrics middleware all use it.

USD prices (per 1M tokens) are bundled in
:data:`DEFAULT_PRICING`. Operators can override them at runtime
via the API.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing catalog
# ---------------------------------------------------------------------------


#: USD per 1M tokens. Keyed by canonical model name.
#: Update this map when providers change rates; the tracker's
#: :func:`estimate_cost` falls back to ``0.0`` for unknown models.
DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o":           {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":      {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":      {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo":    {"input": 0.50,  "output": 1.50},
    "o1":               {"input": 15.00, "output": 60.00},
    "o1-mini":          {"input": 3.00,  "output": 12.00},
    "o3-mini":          {"input": 1.10,  "output": 4.40},
    # Anthropic
    "claude-3-5-sonnet": {"input": 3.00,  "output": 15.00},
    "claude-3-5-haiku":  {"input": 0.80,  "output": 4.00},
    "claude-3-opus":     {"input": 15.00, "output": 75.00},
    "claude-sonnet-4":   {"input": 3.00,  "output": 15.00},
    "claude-opus-4":     {"input": 15.00, "output": 75.00},
    # Local / free
    "ollama":           {"input": 0.0,   "output": 0.0},
    "mock":             {"input": 0.0,   "output": 0.0},
}


def _normalize_model(name: str) -> str:
    """Normalize a model name to a known pricing key.

    Strips a trailing date suffix in either of two forms:
      - ``-YYYY-MM-DD`` (10 chars, dashed)
      - ``-YYYYMMDD``   (8 chars, compact)
    And a leading provider prefix: ``openai/...`` → ``...``.
    """
    import re
    if not name:
        return ""
    n = str(name).strip().lower()
    if "/" in n:
        n = n.split("/", 1)[1]
    n = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", n)
    n = re.sub(r"-\d{8}$", "", n)
    return n


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int,
                  *, pricing: Optional[Dict[str, Dict[str, float]]] = None) -> float:
    """Return estimated USD cost for *prompt_tokens* + *completion_tokens*."""
    if prompt_tokens < 0 or completion_tokens < 0:
        return 0.0
    key = _normalize_model(model)
    catalog = pricing or DEFAULT_PRICING
    rates = catalog.get(key)
    if not rates:
        return 0.0
    in_rate = rates.get("input", 0.0) / 1_000_000.0
    out_rate = rates.get("output", 0.0) / 1_000_000.0
    return prompt_tokens * in_rate + completion_tokens * out_rate


# ---------------------------------------------------------------------------
# Usage records
# ---------------------------------------------------------------------------


@dataclass
class UsageRecord:
    """One LLM call's worth of usage."""
    project_id: str
    agent: str
    role: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""
    round: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class ProjectCostSummary:
    """Per-project aggregation."""
    project_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    by_agent: Dict[str, int] = field(default_factory=dict)
    by_model: Dict[str, int] = field(default_factory=dict)
    by_day: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "by_agent": dict(self.by_agent),
            "by_model": dict(self.by_model),
            "by_day": {k: round(v, 6) for k, v in self.by_day.items()},
        }


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Process-wide token + USD tracker.

    Thread-safe (the orchestrator + WebSocket dispatcher can both
    record on the same instance). Records are kept in memory; the
    orchestrator is expected to drain them to the persistence
    layer on its own schedule.
    """

    def __init__(self, *, pricing: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        self._records: List[UsageRecord] = []
        self._lock = threading.Lock()
        self._pricing = pricing or dict(DEFAULT_PRICING)

    def set_pricing(self, pricing: Dict[str, Dict[str, float]]) -> None:
        with self._lock:
            self._pricing = dict(pricing)

    def get_pricing(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            return dict(self._pricing)

    def record(self, *, project_id: str, agent: str, role: str, model: str,
               prompt_tokens: int, completion_tokens: int,
               session_id: str = "", round_no: int = 0,
               metadata: Optional[Dict[str, str]] = None) -> UsageRecord:
        """Record one LLM call. Returns the new record."""
        cost = estimate_cost(
            model, prompt_tokens, completion_tokens, pricing=self._pricing,
        )
        rec = UsageRecord(
            project_id=project_id,
            agent=agent,
            role=role,
            model=model,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cost_usd=cost,
            session_id=session_id,
            round=round_no,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._records.append(rec)
        return rec

    # -- aggregation ---------------------------------------------------

    def all_records(self) -> List[UsageRecord]:
        with self._lock:
            return list(self._records)

    def by_project(self) -> Dict[str, ProjectCostSummary]:
        out: Dict[str, ProjectCostSummary] = {}
        with self._lock:
            records = list(self._records)
        for r in records:
            s = out.setdefault(r.project_id, ProjectCostSummary(project_id=r.project_id))
            s.prompt_tokens += r.prompt_tokens
            s.completion_tokens += r.completion_tokens
            s.cost_usd += r.cost_usd
            s.calls += 1
            s.by_agent[r.agent] = s.by_agent.get(r.agent, 0) + 1
            s.by_model[r.model] = s.by_model.get(r.model, 0) + 1
            day = time.strftime("%Y-%m-%d", time.gmtime(r.timestamp))
            s.by_day[day] = s.by_day.get(day, 0.0) + r.cost_usd
        return out

    def summary(self) -> Dict[str, float]:
        """Return a global aggregate (across all projects)."""
        with self._lock:
            records = list(self._records)
        total_in = sum(r.prompt_tokens for r in records)
        total_out = sum(r.completion_tokens for r in records)
        total_cost = sum(r.cost_usd for r in records)
        return {
            "calls": len(records),
            "prompt_tokens": total_in,
            "completion_tokens": total_out,
            "total_tokens": total_in + total_out,
            "cost_usd": round(total_cost, 6),
        }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_tracker: Optional[CostTracker] = None
_tracker_lock = threading.Lock()


def get_tracker() -> CostTracker:
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = CostTracker()
    return _tracker


def reset_tracker() -> None:
    global _tracker
    _tracker = None
