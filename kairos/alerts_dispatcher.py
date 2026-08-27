"""Round 28: Real Slack integration + alert history.

R22 added the alert engine + a generic ``send_to_webhook``
function. This round:

  - Adds ``send_to_slack()`` — a higher-level helper that reads
    ``KAIROS_SLACK_WEBHOOK`` from the env and POSTs the
    ``format_slack_payload`` output.
  - Adds ``fire_alerts(alerts)`` — the full pipeline: read
    env, format, send, log to a JSONL history.
  - Adds a CLI: ``python -m kairos.alerts_dispatcher detect
    <baseline.json> <current.json>`` to detect + dispatch
    in one shot. Useful for CI hooks.
  - Persists the alert history at ``<data_dir>/alerts.jsonl``
    so the UI can show "the last 50 alerts".

The dispatcher never raises. A network failure is logged
and recorded in the history as ``status="failed"``; the
caller (CI) can decide whether to fail the build based on
the alert severity.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from kairos.alerts import (
    Alert,
    detect_cost_regressions,
    detect_from_files,
    format_slack_payload,
    send_to_webhook,
    _load_run_costs,
)

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_PATH = Path("data") / "alerts.jsonl"


@dataclass
class FiredAlert:
    """An alert that was processed (sent or skipped)."""
    timestamp: float
    severity: str
    kind: str
    message: str
    metric: str
    baseline: float
    current: float
    delta_pct: float
    channel: str        # "slack" | "webhook" | "none"
    channel_url: str    # masked (just the host) for safety
    status: str        # "sent" | "failed" | "skipped"
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _mask_url(url: str) -> str:
    """Return just the host (for safe logging)."""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}/..." if p.netloc else "(invalid)"
    except Exception:
        return "(unparseable)"


def _get_history_path() -> Path:
    """Resolve the alert history file path.

    Honors ``KAIROS_DATA_DIR`` (default ``./data``)."""
    data_dir = Path(os.environ.get(
        "KAIROS_DATA_DIR",
        Path(__file__).resolve().parent.parent / "data",
    ))
    return data_dir / "alerts.jsonl"


def append_to_history(fired: FiredAlert, path: Optional[Path] = None
                     ) -> None:
    """Append a FiredAlert to the JSONL history (best-effort)."""
    p = path or _get_history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(fired.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
    except OSError as exc:
        logger.debug("history write failed: %s", exc)


def read_history(path: Optional[Path] = None, limit: int = 50
                 ) -> List[Dict[str, Any]]:
    """Return the most recent ``limit`` alerts (newest first)."""
    p = path or _get_history_path()
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l]
        out: List[Dict[str, Any]] = []
        for line in reversed(lines[-limit:]):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except OSError as exc:
        logger.debug("history read failed: %s", exc)
        return []


def send_to_slack(alerts: List[Alert], webhook_url: Optional[str] = None,
                  *, timeout: float = 5.0) -> bool:
    """POST a formatted Slack payload to ``webhook_url``.

    If ``webhook_url`` is None, reads ``KAIROS_SLACK_WEBHOOK``
    from the env. Returns True on 2xx, False otherwise.
    """
    url = webhook_url or os.environ.get("KAIROS_SLACK_WEBHOOK")
    if not url:
        logger.debug("no KAIROS_SLACK_WEBHOOK set; skipping Slack send")
        return False
    payload = format_slack_payload(alerts)
    return send_to_webhook(url, payload, timeout=timeout)


def fire_alerts(alerts: List[Alert], *,
                webhook_url: Optional[str] = None,
                channel: str = "slack",
                history_path: Optional[Path] = None) -> List[FiredAlert]:
    """Dispatch ``alerts`` to the configured channel + persist
    each one to the history.

    Returns a list of ``FiredAlert`` records (one per input
    alert). Network failures are captured per-alert, not raised.
    """
    if not alerts:
        return []
    if channel == "slack":
        url = webhook_url or os.environ.get("KAIROS_SLACK_WEBHOOK")
        if not url:
            # No channel configured → mark all as "skipped"
            out = []
            for a in alerts:
                fired = FiredAlert(
                    timestamp=time.time(),
                    severity=a.severity, kind=a.kind, message=a.message,
                    metric=a.metric, baseline=a.baseline,
                    current=a.current, delta_pct=a.delta_pct,
                    channel="none", channel_url="",
                    status="skipped",
                    error="KAIROS_SLACK_WEBHOOK not set",
                )
                append_to_history(fired, history_path)
                out.append(fired)
            return out
        payload = format_slack_payload(alerts)
        ok = send_to_webhook(url, payload)
        status = "sent" if ok else "failed"
        error = "" if ok else "webhook returned non-2xx"
        masked = _mask_url(url)
    else:
        masked = ""
        status = "skipped"
        error = f"unknown channel: {channel}"
    out = []
    for a in alerts:
        fired = FiredAlert(
            timestamp=time.time(),
            severity=a.severity, kind=a.kind, message=a.message,
            metric=a.metric, baseline=a.baseline,
            current=a.current, delta_pct=a.delta_pct,
            channel=channel, channel_url=masked,
            status=status, error=error,
        )
        append_to_history(fired, history_path)
        out.append(fired)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_alert_row(f: Dict[str, Any]) -> str:
    return (
        f"{f['timestamp']:.0f}  {f['severity'].upper():8s}  "
        f"{f['kind']:14s}  {f['message'][:60]:<60s}  "
        f"[{f['status']}]"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kairos.alerts_dispatcher",
        description="Cost regression alert detector + dispatcher",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    det = sub.add_parser("detect",
                          help="Detect cost regressions between two run JSONs")
    det.add_argument("baseline", help="Baseline run JSON (the known-good one)")
    det.add_argument("current", help="Current run JSON (the suspect one)")
    det.add_argument("--threshold-pct", type=float, default=50.0)
    det.add_argument("--call-threshold-pct", type=float, default=200.0)
    det.add_argument("--calls-threshold-pct", type=float, default=100.0)
    det.add_argument("--no-send", action="store_true",
                     help="Detect only; don't send to Slack")
    det.add_argument("--webhook",
                     help="Override KAIROS_SLACK_WEBHOOK for this run")
    det.add_argument("--json", action="store_true",
                     help="Emit JSON instead of human-readable text")

    hist = sub.add_parser("history",
                          help="Show the recent alert history")
    hist.add_argument("--limit", type=int, default=20)
    hist.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "detect":
        alerts = detect_from_files(
            Path(args.baseline), Path(args.current),
            threshold_pct=args.threshold_pct,
            call_threshold_pct=args.call_threshold_pct,
            calls_threshold_pct=args.calls_threshold_pct,
        )
        if args.json:
            print(json.dumps([a.to_dict() for a in alerts], indent=2))
            return 0 if not alerts or all(a.severity != "critical"
                                          for a in alerts) else 1
        # Human-readable
        if not alerts:
            print(f"No regressions between {args.baseline} and "
                  f"{args.current}.")
            return 0
        print(f"Found {len(alerts)} alert(s):")
        for a in alerts:
            print(f"  [{a.severity.upper()}] {a.kind}: {a.message}")
        # Critical exit code takes precedence (CI can fail even in --no-send)
        has_critical = any(a.severity == "critical" for a in alerts)
        if args.no_send:
            print("(no-send: not dispatched)")
            return 2 if has_critical else 0
        # Dispatch
        fired = fire_alerts(alerts, webhook_url=args.webhook)
        sent = sum(1 for f in fired if f.status == "sent")
        print(f"\nDispatched {sent}/{len(fired)} to Slack")
        # Exit code: 2 on critical alert (so CI can fail)
        return 2 if has_critical else 0
    if args.cmd == "history":
        entries = read_history(limit=args.limit)
        if args.json:
            print(json.dumps(entries, indent=2))
            return 0
        if not entries:
            print("No alerts in history.")
            return 0
        print(f"Recent alerts (newest first, max {args.limit}):")
        for f in entries:
            print("  " + _format_alert_row(f))
        return 0
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
