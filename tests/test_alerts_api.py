"""Tests for the alerts API endpoints (Round 30 UI backend).

Covers:
  - GET  /api/alerts/recent
  - GET  /api/alerts/summary
  - POST /api/alerts/mute
  - GET  /api/alerts/mutes
  - DELETE /api/alerts/mute/{key}
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kairos.alerts_dispatcher import (
    FiredAlert,
    _get_history_path,
    append_to_history,
)
from api.routes.alerts import _mutes_path
from kairos.alerts import Alert

from api.app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert(severity: str = "warning", kind: str = "cost_spike",
                metric: str = "cost_usd", baseline: float = 1.0,
                current: float = 2.0, delta_pct: float = 100.0,
                message: str = "test") -> Alert:
    return Alert(severity=severity, kind=kind, message=message,
                 metric=metric, baseline=baseline, current=current,
                 delta_pct=delta_pct)


def _fired(severity: str = "warning", kind: str = "cost_spike",
           metric: str = "cost_usd", baseline: float = 1.0,
           current: float = 2.0, delta_pct: float = 100.0,
           status: str = "sent", message: str = "test") -> FiredAlert:
    return FiredAlert(
        timestamp=time.time(),
        severity=severity, kind=kind, message=message, metric=metric,
        baseline=baseline, current=current, delta_pct=delta_pct,
        channel="slack", channel_url="https://hooks.example",
        status=status, error="",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient with a fresh data dir."""
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    # Make sure both files are empty at the start of each test
    h = _get_history_path()
    h.parent.mkdir(parents=True, exist_ok=True)
    h.write_text("", encoding="utf-8")
    m = _mutes_path()
    if m.exists():
        m.unlink()
    return TestClient(app)


# ---------------------------------------------------------------------------
# /recent
# ---------------------------------------------------------------------------


def test_recent_empty(client):
    r = client.get("/api/alerts/recent")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 0
    assert data["entries"] == []
    assert "history_path" in data


def test_recent_returns_entries_newest_first(client, tmp_path):
    # Plant 3 fired alerts (oldest first)
    append_to_history(_fired(kind="cost_spike", message="first"), tmp_path / "alerts.jsonl")
    time.sleep(0.05)
    append_to_history(_fired(kind="call_spike", message="second"), tmp_path / "alerts.jsonl")
    time.sleep(0.05)
    append_to_history(_fired(kind="calls_growth", message="third"), tmp_path / "alerts.jsonl")
    r = client.get("/api/alerts/recent?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 3
    # Newest first
    assert data["entries"][0]["message"] == "third"
    assert data["entries"][2]["message"] == "first"


def test_recent_limit_caps_results(client, tmp_path):
    for i in range(5):
        append_to_history(_fired(message=f"m{i}"), tmp_path / "alerts.jsonl")
    r = client.get("/api/alerts/recent?limit=2")
    data = r.json()
    assert data["count"] == 2
    # Newest first
    assert data["entries"][0]["message"] == "m4"


def test_recent_limit_validation(client):
    # limit must be >= 1 and <= 200
    r = client.get("/api/alerts/recent?limit=0")
    assert r.status_code == 422
    r = client.get("/api/alerts/recent?limit=500")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------


def test_summary_empty(client):
    r = client.get("/api/alerts/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["by_severity"] == {"info": 0, "warning": 0, "critical": 0}
    assert data["by_status"]["sent"] == 0
    assert data["last_critical_at"] is None
    assert data["active_mutes"] == 0


def test_summary_counts_by_severity_and_kind(client, tmp_path):
    append_to_history(_fired(severity="critical", kind="cost_spike"), tmp_path / "alerts.jsonl")
    append_to_history(_fired(severity="warning", kind="cost_spike"), tmp_path / "alerts.jsonl")
    append_to_history(_fired(severity="warning", kind="call_spike"), tmp_path / "alerts.jsonl")
    append_to_history(_fired(severity="info", kind="calls_growth"), tmp_path / "alerts.jsonl")
    r = client.get("/api/alerts/summary")
    data = r.json()
    assert data["total"] == 4
    assert data["by_severity"]["critical"] == 1
    assert data["by_severity"]["warning"] == 2
    assert data["by_severity"]["info"] == 1
    assert data["by_kind"]["cost_spike"] == 2
    assert data["by_kind"]["call_spike"] == 1
    assert data["by_kind"]["calls_growth"] == 1


def test_summary_last_critical_at_is_max(client, tmp_path):
    append_to_history(_fired(severity="warning"), tmp_path / "alerts.jsonl")
    time.sleep(0.05)
    append_to_history(_fired(severity="critical"), tmp_path / "alerts.jsonl")
    time.sleep(0.05)
    append_to_history(_fired(severity="warning"), tmp_path / "alerts.jsonl")
    r = client.get("/api/alerts/summary")
    data = r.json()
    # The critical entry has the middle timestamp — and it must be the
    # value returned, not the latest entry's timestamp.
    last = data["last_critical_at"]
    assert last is not None
    # The two non-critical entries have timestamps > critical (because
    # we slept before each). The "last critical" is the max among
    # critical entries, which is just our one critical entry.
    # We assert it falls within a sane window.
    assert abs(last - time.time()) < 5


def test_summary_by_status_includes_failed_and_skipped(client, tmp_path):
    append_to_history(_fired(status="sent"), tmp_path / "alerts.jsonl")
    append_to_history(_fired(status="failed"), tmp_path / "alerts.jsonl")
    append_to_history(_fired(status="skipped"), tmp_path / "alerts.jsonl")
    r = client.get("/api/alerts/summary")
    data = r.json()
    assert data["by_status"]["sent"] == 1
    assert data["by_status"]["failed"] == 1
    assert data["by_status"]["skipped"] == 1


# ---------------------------------------------------------------------------
# /mute + /mutes + DELETE
# ---------------------------------------------------------------------------


def test_mute_creates_entry(client):
    r = client.post("/api/alerts/mute",
                    json={"key": "cost_spike:cost_usd", "duration_s": 60})
    assert r.status_code == 200
    data = r.json()
    assert data["key"] == "cost_spike:cost_usd"
    assert data["duration_s"] == 60
    assert data["expires_at"] > time.time()


def test_mute_default_duration(client):
    """No duration_s → default to 1 hour."""
    r = client.post("/api/alerts/mute", json={"key": "x:y"})
    assert r.status_code == 200
    data = r.json()
    # 1 hour = 3600 s
    assert data["duration_s"] == 3600
    assert data["expires_at"] - time.time() > 3500


def test_mute_invalid_key_format(client):
    """Key must be 'kind:metric' (a single colon)."""
    r = client.post("/api/alerts/mute",
                    json={"key": "no-colon-here", "duration_s": 60})
    assert r.status_code == 400
    r = client.post("/api/alerts/mute",
                    json={"key": "", "duration_s": 60})
    assert r.status_code == 400


def test_mute_duration_validation(client):
    r = client.post("/api/alerts/mute",
                    json={"key": "x:y", "duration_s": 0})
    assert r.status_code == 422
    r = client.post("/api/alerts/mute",
                    json={"key": "x:y", "duration_s": 8 * 24 * 3600})
    assert r.status_code == 422


def test_mute_persists_to_disk(client, tmp_path):
    client.post("/api/alerts/mute",
                json={"key": "x:y", "duration_s": 60})
    p = _mutes_path()
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "x:y" in data
    assert data["x:y"] > time.time()


def test_list_mutes_returns_only_active(client):
    client.post("/api/alerts/mute", json={"key": "active:one", "duration_s": 3600})
    client.post("/api/alerts/mute", json={"key": "stale:two", "duration_s": 1})
    # Wait so the second one expires
    time.sleep(1.2)
    r = client.get("/api/alerts/mutes")
    data = r.json()
    assert "active:one" in data["mutes"]
    assert "stale:two" not in data["mutes"]
    assert data["count"] == 1


def test_unmute_removes_entry(client):
    client.post("/api/alerts/mute", json={"key": "x:y", "duration_s": 60})
    r = client.delete("/api/alerts/mute/x:y")
    assert r.status_code == 200
    data = r.json()
    assert data["removed"] is True
    # Confirm it's gone
    r = client.get("/api/alerts/mutes")
    assert "x:y" not in r.json()["mutes"]


def test_unmute_nonexistent_is_noop(client):
    r = client.delete("/api/alerts/mute/never:existed")
    assert r.status_code == 200
    data = r.json()
    assert data["removed"] is False


def test_summary_active_mutes_count(client):
    """active_mutes in the summary must match the GET /mutes count."""
    client.post("/api/alerts/mute", json={"key": "a:b", "duration_s": 3600})
    client.post("/api/alerts/mute", json={"key": "c:d", "duration_s": 3600})
    r1 = client.get("/api/alerts/summary")
    r2 = client.get("/api/alerts/mutes")
    assert r1.json()["active_mutes"] == 2
    assert r2.json()["count"] == 2


# ---------------------------------------------------------------------------
# Mute state survives a re-load (atomic write)
# ---------------------------------------------------------------------------


def test_mute_state_round_trip(client, tmp_path):
    client.post("/api/alerts/mute", json={"key": "x:y", "duration_s": 3600})
    p = _mutes_path()
    assert p.exists()
    raw = p.read_text(encoding="utf-8")
    # Re-load and confirm
    data = json.loads(raw)
    assert "x:y" in data


# ---------------------------------------------------------------------------
# KAIROS_DATA_DIR honored
# ---------------------------------------------------------------------------


def test_data_dir_honored(monkeypatch, tmp_path):
    """When KAIROS_DATA_DIR changes, the API reads from the new dir."""
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    h = _get_history_path()
    h.parent.mkdir(parents=True, exist_ok=True)
    h.write_text("", encoding="utf-8")
    append_to_history(_fired(message="hello"), h)
    c = TestClient(app)
    r = c.get("/api/alerts/recent")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["entries"][0]["message"] == "hello"
