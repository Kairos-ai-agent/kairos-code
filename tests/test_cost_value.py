"""Tests for the COGS (cost of goods sold) value endpoint (Round 35).

Covers:
  - GET /api/cost/value: empty state
  - With cost + datasets + alerts data populated
  - cost_per_case, cost_per_passing, cost_per_alert, efficiency, approval_yield
  - Zero-denominator returns None (not 0 or NaN)
  - Bad JSONL lines skipped
  - KAIROS_DATA_DIR honored
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kairos.cost import _get_log_path
from kairos.alerts_dispatcher import _get_history_path, FiredAlert, append_to_history

from api.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient with a fresh data dir.

    Clears the kairos.cost._LOG_PATH cache (R11 lesson: path lookup
    is call-time, but the module caches the first computed path).
    """
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    # Clear the cached log path so _get_log_path() re-reads the env
    from kairos import cost as cost_mod
    cost_mod._LOG_PATH = None
    # Also clear the alerts history cache
    from kairos import alerts_dispatcher
    # Make sure both files are empty
    cost_p = _get_log_path()
    cost_p.parent.mkdir(parents=True, exist_ok=True)
    cost_p.write_text("", encoding="utf-8")
    alert_p = _get_history_path()
    if alert_p.exists():
        alert_p.unlink()
    return TestClient(app)


def _write_cost(client, entries: list) -> None:
    """Append cost entries to data/cost.jsonl."""
    p = _get_log_path()
    with open(p, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _write_dataset(client, name: str, cases: list) -> None:
    """Write a dataset JSONL file."""
    p = _get_log_path().parent / "datasets" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")


def _write_alert(client, severity: str = "warning") -> None:
    """Append a fired alert."""
    f = FiredAlert(
        timestamp=0, severity=severity, kind="x", message="m",
        metric="cost_usd", baseline=0, current=0, delta_pct=0,
        channel="slack", channel_url="h", status="sent", error="",
    )
    p = _get_history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    append_to_history(f, p)


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_value_empty_returns_none_metrics(client):
    r = client.get("/api/cost/value")
    assert r.status_code == 200
    data = r.json()
    assert data["total_cost_usd"] == 0
    assert data["n_llm_calls"] == 0
    assert data["dataset"] == {"total_cases": 0, "passed": 0, "failed": 0}
    assert data["alerts"] == {"total": 0, "critical": 0}
    # All metrics are None (zero denominators)
    for k, v in data["metrics"].items():
        assert v is None, f"{k} should be None for empty state, got {v}"


# ---------------------------------------------------------------------------
# Cost only
# ---------------------------------------------------------------------------


def test_value_with_only_cost(client):
    _write_cost(client, [
        {"timestamp": 0, "model": "gpt-4o", "cost_usd": 0.50},
        {"timestamp": 1, "model": "gpt-4o", "cost_usd": 0.30},
    ])
    r = client.get("/api/cost/value")
    data = r.json()
    assert data["total_cost_usd"] == pytest.approx(0.80)
    assert data["n_llm_calls"] == 2
    # No cases, so cost_per_case = None
    assert data["metrics"]["cost_per_case"] is None
    assert data["metrics"]["cost_per_passing"] is None
    # No alerts
    assert data["metrics"]["cost_per_alert"] is None


# ---------------------------------------------------------------------------
# Datasets only
# ---------------------------------------------------------------------------


def test_value_with_only_datasets(client):
    _write_dataset(client, "r1.jsonl", [
        {"name": "a", "passed": True},
        {"name": "b", "passed": True},
        {"name": "c", "passed": False},
    ])
    r = client.get("/api/cost/value")
    data = r.json()
    assert data["dataset"] == {"total_cases": 3, "passed": 2, "failed": 1}
    assert data["metrics"]["efficiency"] == pytest.approx(0.666667, rel=1e-3)
    # No cost → 0 cost per case (0 / 3 = 0)
    assert data["metrics"]["cost_per_case"] == 0.0
    assert data["metrics"]["cost_per_passing"] == 0.0
    assert data["metrics"]["cost_per_alert"] is None  # no alerts
    assert data["metrics"]["approval_yield"] is None  # no alerts


# ---------------------------------------------------------------------------
# Full state
# ---------------------------------------------------------------------------


def test_value_full_state_computes_ratios(client):
    _write_cost(client, [
        {"timestamp": 0, "model": "gpt-4o", "cost_usd": 0.60},
        {"timestamp": 1, "model": "gpt-4o", "cost_usd": 0.40},
    ])
    _write_dataset(client, "r1.jsonl", [
        {"name": "a", "passed": True},
        {"name": "b", "passed": True},
        {"name": "c", "passed": True},
        {"name": "d", "passed": False},
    ])
    _write_alert(client, severity="warning")
    _write_alert(client, severity="warning")
    _write_alert(client, severity="critical")
    r = client.get("/api/cost/value")
    data = r.json()
    assert data["total_cost_usd"] == pytest.approx(1.00)
    assert data["n_llm_calls"] == 2
    assert data["dataset"] == {"total_cases": 4, "passed": 3, "failed": 1}
    assert data["alerts"] == {"total": 3, "critical": 1}
    m = data["metrics"]
    # 1.0 / 4 cases = 0.25
    assert m["cost_per_case"] == pytest.approx(0.25)
    # 1.0 / 3 passing = ~0.333
    assert m["cost_per_passing"] == pytest.approx(0.333333, rel=1e-3)
    # 1.0 / 3 alerts = ~0.333
    assert m["cost_per_alert"] == pytest.approx(0.333333, rel=1e-3)
    # 3/4 = 0.75
    assert m["efficiency"] == pytest.approx(0.75)
    # 1 - 1/3 = 0.666...
    assert m["approval_yield"] == pytest.approx(0.666667, rel=1e-3)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_value_skips_corrupt_cost_lines(client):
    p = _get_log_path()
    p.write_text(
        json.dumps({"timestamp": 0, "cost_usd": 0.10}) + "\n"
        + "{not json\n"
        + json.dumps({"timestamp": 1, "cost_usd": 0.20}) + "\n",
        encoding="utf-8",
    )
    r = client.get("/api/cost/value")
    data = r.json()
    # Only 2 valid lines counted
    assert data["total_cost_usd"] == pytest.approx(0.30)
    assert data["n_llm_calls"] == 2


def test_value_skips_corrupt_alert_lines(client):
    p = _get_history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"timestamp": 0, "severity": "warning",
                    "kind": "k", "message": "m", "metric": "x",
                    "baseline": 0, "current": 0, "delta_pct": 0,
                    "channel": "slack", "channel_url": "h",
                    "status": "sent", "error": ""}) + "\n"
        + "{not json\n",
        encoding="utf-8",
    )
    r = client.get("/api/cost/value")
    data = r.json()
    # Only 1 valid alert
    assert data["alerts"]["total"] == 1


def test_value_skips_corrupt_dataset_lines(client):
    p = _get_log_path().parent / "datasets" / "x.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"name": "a", "passed": True}) + "\n"
        + "{not json\n"
        + json.dumps({"name": "b", "passed": False}) + "\n",
        encoding="utf-8",
    )
    r = client.get("/api/cost/value")
    data = r.json()
    # 2 valid cases, 1 passed, 1 failed
    assert data["dataset"]["total_cases"] == 2
    assert data["dataset"]["passed"] == 1
    assert data["dataset"]["failed"] == 1


def test_value_approval_yield_none_when_no_alerts(client):
    """approval_yield requires at least one alert (denominator)."""
    _write_dataset(client, "r1.jsonl", [{"name": "a", "passed": True}])
    r = client.get("/api/cost/value")
    assert r.json()["metrics"]["approval_yield"] is None


def test_value_handles_dataset_with_only_failures(client):
    """All cases fail → efficiency = 0.0, not None."""
    _write_dataset(client, "r1.jsonl", [
        {"name": "a", "passed": False},
        {"name": "b", "passed": False},
    ])
    r = client.get("/api/cost/value")
    data = r.json()
    assert data["dataset"]["passed"] == 0
    assert data["metrics"]["efficiency"] == 0.0


def test_value_kairos_data_dir_honored(monkeypatch, tmp_path):
    """When KAIROS_DATA_DIR changes, the endpoint reads from the new dir."""
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    cost_p = _get_log_path()
    cost_p.parent.mkdir(parents=True, exist_ok=True)
    cost_p.write_text(
        json.dumps({"timestamp": 0, "cost_usd": 0.10}) + "\n",
        encoding="utf-8",
    )
    client = TestClient(app)
    r = client.get("/api/cost/value")
    assert r.json()["total_cost_usd"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Multi-dataset aggregation
# ---------------------------------------------------------------------------


def test_value_aggregates_across_multiple_datasets(client):
    """Cases from all *.jsonl files in datasets/ are summed."""
    _write_dataset(client, "r1.jsonl", [
        {"name": "a", "passed": True},
        {"name": "b", "passed": True},
    ])
    _write_dataset(client, "r2.jsonl", [
        {"name": "c", "passed": False},
        {"name": "d", "passed": True},
    ])
    r = client.get("/api/cost/value")
    data = r.json()
    assert data["dataset"]["total_cases"] == 4
    assert data["dataset"]["passed"] == 3
    assert data["dataset"]["failed"] == 1
    assert data["metrics"]["efficiency"] == pytest.approx(0.75)
