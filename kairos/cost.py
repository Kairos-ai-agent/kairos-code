"""Round 14: per-call cost tracking.

``litellm`` ships a built-in cost table: every supported model
has a known USD price per 1K tokens. By passing a
``success_callback`` to ``litellm.completion``, the library
computes the cost of every call and surfaces it via a callback
function. This module wires that callback up so every LLM
call records ``prompt_tokens``, ``completion_tokens``, and
``cost_usd`` to:

  1. an in-memory ring buffer (always available; tests use this)
  2. a JSONL log file on disk (rotated by date; for the cost
     dashboard)
  3. a Langfuse / OTel sink (via the observability tracer, if
     the user has one configured)

The cost dashboard is then a single `cat .kairos/cost.jsonl |
jq` away; the user can pipe it to the FrontEnd's cost panel
(R15 candidate).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CostEntry:
    """One LLM call's cost record."""
    timestamp: float
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_ms: int
    call_id: str = ""
    # Round 14: trace correlation. If the OTel tracer is active,
    # this is the span's trace_id; the cost dashboard can join
    # the cost entry to the corresponding trace.
    trace_id: str = ""


# Module-level ring buffer (process-wide). Tests inspect this.
_BUFFER: Deque[CostEntry] = deque(maxlen=10_000)
_LOCK = threading.Lock()
# Persistent JSONL sink — opened lazily on first write
_LOG_PATH: Optional[Path] = None
_LOG_FH = None


def _get_log_path() -> Optional[Path]:
    """Resolve the cost log path, defaulting to ``<data_dir>/cost.jsonl``."""
    global _LOG_PATH
    if _LOG_PATH is not None:
        return _LOG_PATH
    data_dir = Path(os.environ.get(
        "KAIROS_DATA_DIR",
        Path(__file__).resolve().parent.parent / "data",
    ))
    _LOG_PATH = data_dir / "cost.jsonl"
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _LOG_PATH


def set_log_path(path: Path) -> None:
    """Override the default cost log path (used by tests)."""
    global _LOG_PATH, _LOG_FH
    if _LOG_FH is not None:
        try:
            _LOG_FH.close()
        except Exception:
            pass
    _LOG_FH = None
    _LOG_PATH = path
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_buffer() -> List[CostEntry]:
    """Return a snapshot of the in-memory cost buffer."""
    with _LOCK:
        return list(_BUFFER)


def clear_buffer() -> None:
    """Clear the in-memory cost buffer (tests only)."""
    with _LOCK:
        _BUFFER.clear()


def total_cost() -> float:
    """Sum of all cost entries in the in-memory buffer."""
    with _LOCK:
        return sum(e.cost_usd for e in _BUFFER)


def record_entry(
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    *,
    provider: str = "unknown",
    duration_ms: int = 0,
    call_id: str = "",
) -> CostEntry:
    """Record one LLM call in the ledger (buffer + JSONL).

    The litellm path had a callback; providers that do not go through litellm
    (local models, the scripted provider used by ``kairos demo``) had no way to
    appear in the cost dashboard at all, so their calls were invisible. This is
    that entry point: pass the real numbers when you have them, or 0.0 when the
    call genuinely costs nothing (e.g. a local model).
    """
    entry = CostEntry(
        timestamp=time.time(),
        model=str(model),
        provider=str(provider),
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=int(completion_tokens or 0),
        cost_usd=float(cost_usd or 0.0),
        duration_ms=int(duration_ms or 0),
        call_id=str(call_id or ""),
    )
    with _LOCK:
        _BUFFER.append(entry)
    try:
        path = _get_log_path()
        if path is not None:
            global _LOG_FH
            if _LOG_FH is None:
                _LOG_FH = open(path, "a", encoding="utf-8")
            _LOG_FH.write(json.dumps(asdict(entry)) + "\n")
            _LOG_FH.flush()
    except Exception as exc:  # never break a call because the ledger failed
        logger.debug("cost log write failed: %s", exc)
    return entry


def litellm_cost_callback(
    kwargs: Dict[str, Any],
    completion_response: Any,
    start_time: float,
    end_time: float,
) -> None:
    """litellm ``success_callback`` hook.

    The signature is fixed by litellm: every LLM call passes
    these 4 args. We extract model + token usage + cost from
    the response and record a CostEntry.

    Cost computation: litellm computes ``cost_usd`` from its
    price table and sets it on the response object as
    ``_hidden_params["response_cost"]`` (litellm 1.40+). For
    older versions the field is missing; we fall back to 0.0.
    """
    try:
        # Model name
        model = kwargs.get("model", "unknown")
        # Provider inference: explicit prefix like "anthropic/..."
        # → "anthropic"; bare names like "gpt-4o" map to "openai";
        # unknown names map to "unknown".
        if "/" in model:
            provider = model.split("/", 1)[0]
        elif model.startswith(("gpt-", "o1-", "o3-", "o4-", "text-embedding")):
            provider = "openai"
        elif model.startswith(("claude-", "claude_")):
            provider = "anthropic"
        elif model.startswith("gemini-"):
            provider = "google"
        elif model.startswith("mistral-"):
            provider = "mistral"
        elif model.startswith("command"):
            provider = "cohere"
        else:
            provider = "unknown"
        # Token usage — already extracted by litellm
        usage = getattr(completion_response, "usage", None) or {}
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        # Cost — check litellm's hidden params first
        cost_usd = 0.0
        hidden = getattr(completion_response, "_hidden_params", None) or {}
        if "response_cost" in hidden:
            try:
                cost_usd = float(hidden["response_cost"])
            except (TypeError, ValueError):
                cost_usd = 0.0
        # Build the entry
        entry = CostEntry(
            timestamp=time.time(),
            model=str(model),
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            duration_ms=int((end_time - start_time) * 1000),
        )
        # In-memory ring buffer
        with _LOCK:
            _BUFFER.append(entry)
        # JSONL disk sink (best-effort; never breaks the call)
        try:
            path = _get_log_path()
            if path is not None:
                global _LOG_FH
                if _LOG_FH is None:
                    _LOG_FH = open(path, "a", encoding="utf-8")
                _LOG_FH.write(json.dumps(asdict(entry)) + "\n")
                _LOG_FH.flush()
        except Exception as exc:
            logger.debug("cost log write failed: %s", exc)
        # OTel: tag the active span (if any) with the cost. This
        # way the cost is visible in the Langfuse / OTel UI
        # alongside the trace.
        try:
            from kairos.observability import get_default_tracer
            tracer = get_default_tracer()
            if tracer._otel_tracer is not None and tracer._otel_tracer:
                # In OTel mode the cost is attached to the active
                # current span via set_attribute on the context
                # token. We don't have direct access here, so we
                # emit a fresh no-op span attribute via the
                # current span. This is best-effort.
                from opentelemetry import trace as _trace
                span = _trace.get_current_span()
                if span and span.is_recording():
                    span.set_attribute("gen_ai.usage.cost", cost_usd)
        except Exception:
            pass
    except Exception as exc:
        # NEVER let a cost-tracking error break the LLM call.
        logger.debug("cost callback failed (non-fatal): %s", exc)


def install_cost_callbacks() -> int:
    """Register ``litellm_cost_callback`` with litellm.

    Returns the number of callbacks installed (1 on success, 0
    if litellm isn't installed). Idempotent: safe to call
    multiple times.
    """
    try:
        import litellm  # type: ignore
    except ImportError:
        logger.debug("litellm not installed; cost callbacks disabled")
        return 0
    # Idempotency check
    if litellm_cost_callback in getattr(litellm, "success_callback", []):
        return 1
    litellm.success_callback = list(getattr(litellm, "success_callback", [])) + [
        litellm_cost_callback
    ]
    return 1


# ---------------------------------------------------------------------------
# Cost aggregation helpers
# ---------------------------------------------------------------------------


def cost_by_model() -> Dict[str, Dict[str, Any]]:
    """Aggregate the in-memory cost buffer by model.

    Returns ``{model: {calls, prompt_tokens, completion_tokens,
    cost_usd, avg_duration_ms}}`` for quick dashboard rendering.
    """
    with _LOCK:
        by_model: Dict[str, Dict[str, Any]] = {}
        for e in _BUFFER:
            slot = by_model.setdefault(e.model, {
                "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cost_usd": 0.0, "total_duration_ms": 0,
            })
            slot["calls"] += 1
            slot["prompt_tokens"] += e.prompt_tokens
            slot["completion_tokens"] += e.completion_tokens
            slot["cost_usd"] += e.cost_usd
            slot["total_duration_ms"] += e.duration_ms
        # Compute averages
        for v in by_model.values():
            n = v["calls"] or 1
            v["avg_duration_ms"] = v["total_duration_ms"] // n
            del v["total_duration_ms"]
        return by_model


def cost_summary() -> Dict[str, Any]:
    """Return a one-shot summary suitable for a dashboard."""
    with _LOCK:
        total = sum(e.cost_usd for e in _BUFFER)
        n = len(_BUFFER)
        if n == 0:
            return {"calls": 0, "cost_usd": 0.0, "models": {}}
    return {
        "calls": n,
        "cost_usd": total,
        "models": cost_by_model(),
    }
