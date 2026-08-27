"""Round 22: cost regression alerts.

Compares the cost of a *current* eval run against a *baseline*
run and fires an alert when:

  - Total cost increased by more than ``threshold_pct`` (default 50%)
  - Any single call's cost increased by more than
    ``call_threshold_pct`` (default 200%)
  - The number of calls grew by more than ``calls_threshold_pct``
    (default 100%)

Alerts are returned as a list of ``Alert`` dicts that the
caller can route to a webhook, log, or display in the UI.
This module doesn't *send* notifications — that's the
dispatcher's job (round 22.2). The split keeps the engine
unit-testable without touching the network.

Usage:
    alerts = detect_cost_regressions(baseline, current, threshold_pct=50)
    for a in alerts:
        send_to_slack(a)
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """A single regression signal."""
    severity: str          # "info" | "warning" | "critical"
    kind: str              # "cost_spike" | "call_spike" | "calls_growth"
    message: str           # human-readable
    metric: str            # "cost_usd" | "per_call" | "n_calls"
    baseline: float
    current: float
    delta_pct: float       # (current - baseline) / baseline * 100

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_run_costs(run_path: Path) -> Dict[str, float]:
    """Load total cost + per-call cost from a saved run JSON.

    The SuiteResult JSON has ``cases: [{cost_usd: ...}, ...]``.
    Cases with non-numeric cost values are skipped (counted as
    0 in the total but not included in per_call or n_calls).
    """
    try:
        with open(run_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("failed to load run %s: %s", run_path, exc)
        return {}
    cases = data.get("cases", []) or []
    total = 0.0
    per_call: List[float] = []
    for c in cases:
        raw = c.get("cost_usd", 0.0)
        if raw is None or raw == "":
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        total += v
        per_call.append(v)
    return {
        "total": total,
        "per_call": per_call,
        "n_calls": len(per_call),
    }


def detect_cost_regressions(
    baseline: Dict[str, float],
    current: Dict[str, float],
    *,
    threshold_pct: float = 50.0,
    call_threshold_pct: float = 200.0,
    calls_threshold_pct: float = 100.0,
) -> List[Alert]:
    """Return a list of Alert objects for any regression found.

    The three knobs (cost, per-call, calls) are independent; an
    empty list means "no regressions".

    - ``threshold_pct`` (default 50): total cost % increase
      → "warning" alert
    - ``threshold_pct * 2`` (default 100): → "critical" alert
    - ``call_threshold_pct`` (default 200): per-call p95 % increase
    - ``calls_threshold_pct`` (default 100): n_calls % increase
    """
    alerts: List[Alert] = []
    if not baseline or not current:
        return alerts

    # --- Total cost ---
    b_total = float(baseline.get("total", 0.0))
    c_total = float(current.get("total", 0.0))
    if b_total > 0:
        delta_pct = (c_total - b_total) / b_total * 100.0
        if delta_pct > threshold_pct * 2:
            alerts.append(Alert(
                severity="critical",
                kind="cost_spike",
                message=(f"Total cost spiked {delta_pct:.0f}% "
                         f"(${b_total:.4f} → ${c_total:.4f})"),
                metric="cost_usd",
                baseline=b_total, current=c_total,
                delta_pct=delta_pct,
            ))
        elif delta_pct > threshold_pct:
            alerts.append(Alert(
                severity="warning",
                kind="cost_spike",
                message=(f"Total cost up {delta_pct:.0f}% "
                         f"(${b_total:.4f} → ${c_total:.4f})"),
                metric="cost_usd",
                baseline=b_total, current=c_total,
                delta_pct=delta_pct,
            ))

    # --- Per-call (p95) cost ---
    b_pc = baseline.get("per_call", []) or []
    c_pc = current.get("per_call", []) or []
    if b_pc and c_pc:
        b_p95 = statistics.quantiles(b_pc, n=20)[-1] if len(b_pc) >= 5 else max(b_pc)
        c_p95 = statistics.quantiles(c_pc, n=20)[-1] if len(c_pc) >= 5 else max(c_pc)
        if b_p95 > 0:
            delta_pct = (c_p95 - b_p95) / b_p95 * 100.0
            if delta_pct > call_threshold_pct:
                alerts.append(Alert(
                    severity="warning",
                    kind="call_spike",
                    message=(f"Per-call p95 cost up {delta_pct:.0f}% "
                             f"(${b_p95:.6f} → ${c_p95:.6f})"),
                    metric="per_call",
                    baseline=b_p95, current=c_p95,
                    delta_pct=delta_pct,
                ))

    # --- Call count ---
    b_n = int(baseline.get("n_calls", 0))
    c_n = int(current.get("n_calls", 0))
    if b_n > 0:
        delta_pct = (c_n - b_n) / b_n * 100.0
        if delta_pct > calls_threshold_pct:
            alerts.append(Alert(
                severity="warning",
                kind="calls_growth",
                message=(f"LLM call count grew {delta_pct:.0f}% "
                         f"({b_n} → {c_n})"),
                metric="n_calls",
                baseline=b_n, current=c_n,
                delta_pct=delta_pct,
            ))

    return alerts


def detect_from_files(
    baseline_path: Path,
    current_path: Path,
    **kwargs,
) -> List[Alert]:
    """Convenience: load two run JSONs and detect regressions."""
    return detect_cost_regressions(
        _load_run_costs(baseline_path),
        _load_run_costs(current_path),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Webhook dispatcher
# ---------------------------------------------------------------------------


def format_slack_payload(alerts: List[Alert]) -> Dict[str, Any]:
    """Format alerts as a Slack-compatible incoming-webhook payload."""
    if not alerts:
        return {"text": "Kairos: no cost regressions."}
    blocks: List[Dict[str, Any]] = [{
        "type": "header",
        "text": {"type": "plain_text",
                 "text": f"Kairos: {len(alerts)} cost alert(s)"},
    }]
    for a in alerts:
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
            a.severity, "•"
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{emoji} *[{a.severity.upper()}]* {a.kind}\n"
                             f"  {a.message}\n"
                             f"  baseline={a.baseline:.4f}, "
                             f"current={a.current:.4f}, "
                             f"delta=+{a.delta_pct:.0f}%"},
        })
    return {"blocks": blocks, "text": "Kairos cost alerts"}


def send_to_webhook(url: str, payload: Dict[str, Any],
                    timeout: float = 5.0) -> bool:
    """POST ``payload`` to ``url``. Returns True on 2xx, False on error.

    Lazy-import urllib so this module is importable in any
    environment (some containers strip ssl; we still want the
    alert-detection code to be testable).
    """
    import json
    import urllib.request
    import urllib.error
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        logger.warning("webhook POST failed: %s", exc)
        return False
