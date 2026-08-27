"""Tests for kairos.alerts (Round 22 cost regression detection)."""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from kairos.alerts import (
    Alert,
    detect_cost_regressions,
    detect_from_files,
    format_slack_payload,
    send_to_webhook,
    _load_run_costs,
)


# ---------------------------------------------------------------------------
# _load_run_costs
# ---------------------------------------------------------------------------


def test_load_run_costs_extracts_total_and_per_call(tmp_path: Path):
    run = tmp_path / "r.json"
    run.write_text(json.dumps({
        "cases": [
            {"name": "a", "cost_usd": 0.01},
            {"name": "b", "cost_usd": 0.02},
            {"name": "c", "cost_usd": 0.005},
        ],
    }), encoding="utf-8")
    out = _load_run_costs(run)
    assert out["total"] == pytest.approx(0.035)
    assert out["n_calls"] == 3
    assert out["per_call"] == [0.01, 0.02, 0.005]


def test_load_run_costs_handles_missing_file(tmp_path: Path):
    assert _load_run_costs(tmp_path / "missing.json") == {}


def test_load_run_costs_handles_corrupt(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert _load_run_costs(p) == {}


def test_load_run_costs_skips_invalid_cost_values(tmp_path: Path):
    """A case with non-numeric cost_usd is skipped, not crashed on."""
    run = tmp_path / "r.json"
    run.write_text(json.dumps({
        "cases": [
            {"name": "a", "cost_usd": 0.01},
            {"name": "b", "cost_usd": "not-a-number"},
            {"name": "c", "cost_usd": None},
            {"name": "d", "cost_usd": 0.02},
        ],
    }), encoding="utf-8")
    out = _load_run_costs(run)
    assert out["total"] == pytest.approx(0.03)
    assert out["n_calls"] == 2  # the 2 valid cases


# ---------------------------------------------------------------------------
# detect_cost_regressions — total cost
# ---------------------------------------------------------------------------


def test_no_regression_returns_empty_list():
    baseline = {"total": 1.0, "per_call": [0.1, 0.2, 0.3], "n_calls": 3}
    current = {"total": 1.05, "per_call": [0.1, 0.2, 0.3], "n_calls": 3}
    assert detect_cost_regressions(baseline, current) == []


def test_cost_spike_above_threshold_emits_warning():
    baseline = {"total": 1.0, "per_call": [0.5], "n_calls": 2}
    current = {"total": 1.6, "per_call": [0.5], "n_calls": 2}  # +60%
    alerts = detect_cost_regressions(baseline, current, threshold_pct=50)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "warning"
    assert a.kind == "cost_spike"
    assert "60%" in a.message


def test_cost_spike_double_threshold_is_critical():
    baseline = {"total": 1.0, "per_call": [0.5], "n_calls": 2}
    current = {"total": 2.5, "per_call": [0.5], "n_calls": 2}  # +150%
    alerts = detect_cost_regressions(baseline, current, threshold_pct=50)
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"


def test_cost_decrease_no_alert():
    baseline = {"total": 2.0, "per_call": [0.5], "n_calls": 4}
    current = {"total": 1.0, "per_call": [0.5], "n_calls": 2}  # cheaper!
    assert detect_cost_regressions(baseline, current) == []


# ---------------------------------------------------------------------------
# detect_cost_regressions — per-call
# ---------------------------------------------------------------------------


def test_per_call_spike_emits_warning():
    """When individual calls get more expensive, an alert fires even
    if the total is roughly stable."""
    # 20 calls each at 0.01 → baseline
    baseline = {
        "total": 0.2,
        "per_call": [0.01] * 20,
        "n_calls": 20,
    }
    # Same n_calls, but p95 jumped from 0.01 to 0.05
    current = {
        "total": 0.4,
        "per_call": [0.005] * 5 + [0.05] * 5 + [0.01] * 10,
        "n_calls": 20,
    }
    alerts = detect_cost_regressions(baseline, current, call_threshold_pct=200)
    # Expect at least one call_spike alert
    kinds = [a.kind for a in alerts]
    assert "call_spike" in kinds


def test_per_call_stable_no_alert():
    baseline = {
        "total": 0.2,
        "per_call": [0.01] * 20,
        "n_calls": 20,
    }
    current = {
        "total": 0.21,
        "per_call": [0.011] * 20,
        "n_calls": 20,
    }
    # Per-call up 10% only → no alert
    alerts = detect_cost_regressions(baseline, current, call_threshold_pct=200)
    assert not any(a.kind == "call_spike" for a in alerts)


# ---------------------------------------------------------------------------
# detect_cost_regressions — call count
# ---------------------------------------------------------------------------


def test_calls_growth_emits_warning():
    baseline = {"total": 0.2, "per_call": [0.01] * 20, "n_calls": 20}
    current = {"total": 0.5, "per_call": [0.01] * 50, "n_calls": 50}  # +150%
    alerts = detect_cost_regressions(baseline, current, calls_threshold_pct=100)
    assert any(a.kind == "calls_growth" for a in alerts)


# ---------------------------------------------------------------------------
# detect_from_files
# ---------------------------------------------------------------------------


def test_detect_from_files_end_to_end(tmp_path: Path):
    baseline = tmp_path / "base.json"
    current = tmp_path / "curr.json"
    baseline.write_text(json.dumps({
        "cases": [{"name": f"c{i}", "cost_usd": 0.01} for i in range(5)],
    }), encoding="utf-8")
    current.write_text(json.dumps({
        "cases": [{"name": f"c{i}", "cost_usd": 0.05} for i in range(5)],
    }), encoding="utf-8")
    alerts = detect_from_files(baseline, current, threshold_pct=50)
    assert len(alerts) >= 1
    # Total went from 0.05 to 0.25 — +400% → critical
    severities = [a.severity for a in alerts]
    assert "critical" in severities


# ---------------------------------------------------------------------------
# format_slack_payload
# ---------------------------------------------------------------------------


def test_format_slack_payload_empty_alerts():
    p = format_slack_payload([])
    assert p["text"] == "Kairos: no cost regressions."


def test_format_slack_payload_with_alerts():
    alerts = [
        Alert(severity="warning", kind="cost_spike",
              message="up 60%", metric="cost_usd",
              baseline=1.0, current=1.6, delta_pct=60.0),
        Alert(severity="critical", kind="calls_growth",
              message="grew 200%", metric="n_calls",
              baseline=10, current=30, delta_pct=200.0),
    ]
    p = format_slack_payload(alerts)
    # The header text includes the alert count
    assert "2" in p["blocks"][0]["text"]["text"]
    assert "cost alert" in p["blocks"][0]["text"]["text"]
    assert len(p["blocks"]) == 1 + 2  # header + 2 alerts
    # Each section has the kind and message
    texts = " ".join(b.get("text", {}).get("text", "")
                     for b in p["blocks"] if b["type"] == "section")
    assert "cost_spike" in texts
    assert "calls_growth" in texts
    assert "warning" in texts.lower() or "WARNING" in texts


# ---------------------------------------------------------------------------
# send_to_webhook
# ---------------------------------------------------------------------------


def test_send_to_webhook_success():
    """A 2xx response returns True."""
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    fake.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake):
        ok = send_to_webhook("https://hooks.example/x", {"text": "hi"})
    assert ok is True


def test_send_to_webhook_4xx_returns_false():
    """A 4xx response returns False (no exception)."""
    fake = MagicMock()
    fake.__enter__.return_value.status = 404
    fake.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake):
        ok = send_to_webhook("https://hooks.example/x", {"text": "hi"})
    assert ok is False


def test_send_to_webhook_5xx_returns_false():
    """A 5xx response returns False."""
    fake = MagicMock()
    fake.__enter__.return_value.status = 500
    fake.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake):
        ok = send_to_webhook("https://hooks.example/x", {"text": "hi"})
    assert ok is False


def test_send_to_webhook_network_error_returns_false():
    """A network failure (URLError) returns False, doesn't crash."""
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("connection refused")):
        ok = send_to_webhook("https://hooks.example/x", {"text": "hi"})
    assert ok is False
