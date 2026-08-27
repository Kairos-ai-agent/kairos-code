"""Tests for kairos.alerts_dispatcher (Round 28).

Covers:
  - _mask_url
  - send_to_slack (env-var wiring)
  - fire_alerts pipeline (sent / failed / skipped paths)
  - append_to_history + read_history (JSONL round-trip)
  - _get_history_path honors KAIROS_DATA_DIR
  - CLI: `detect` (text + json + --no-send + exit codes)
  - CLI: `history` (text + json + --limit)
"""
from __future__ import annotations

import io
import json
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from kairos.alerts_dispatcher import (
    FiredAlert,
    _get_history_path,
    _mask_url,
    append_to_history,
    fire_alerts,
    main,
    read_history,
    send_to_slack,
)
from kairos.alerts import Alert


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert(severity: str = "warning", kind: str = "cost_spike",
                baseline: float = 1.0, current: float = 2.0,
                delta_pct: float = 100.0) -> Alert:
    return Alert(
        severity=severity,
        kind=kind,
        message=f"test {kind} {severity}",
        metric="cost_usd",
        baseline=baseline,
        current=current,
        delta_pct=delta_pct,
    )


# ---------------------------------------------------------------------------
# _mask_url
# ---------------------------------------------------------------------------


def test_mask_url_returns_host_only():
    out = _mask_url("https://hooks.slack.com/services/T0/B0/XXXsecret")
    # Must not leak the path/secret
    assert "XXXsecret" not in out
    assert "hooks.slack.com" in out
    assert out.startswith("https://")


def test_mask_url_invalid_returns_placeholder():
    # urlparse accepts almost anything; truly broken input gets "(invalid)"
    assert _mask_url("") in ("(invalid)", "(unparseable)")


# ---------------------------------------------------------------------------
# _get_history_path
# ---------------------------------------------------------------------------


def test_get_history_path_honors_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    p = _get_history_path()
    assert p == tmp_path / "alerts.jsonl"


def test_get_history_path_call_time_env(monkeypatch, tmp_path: Path):
    """R11 lesson: path lookup is call-time, not import-time."""
    # Don't pre-set the env; the function should pick it up now
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    p = _get_history_path()
    assert p.parent == tmp_path


# ---------------------------------------------------------------------------
# send_to_slack
# ---------------------------------------------------------------------------


def test_send_to_slack_no_env_returns_false(monkeypatch):
    monkeypatch.delenv("KAIROS_SLACK_WEBHOOK", raising=False)
    assert send_to_slack([_make_alert()]) is False


def test_send_to_slack_with_env_calls_send(monkeypatch):
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://hooks.example/x")
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    fake.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake):
        ok = send_to_slack([_make_alert(), _make_alert(severity="critical",
                                                        kind="call_spike")])
    assert ok is True


def test_send_to_slack_explicit_url_overrides_env(monkeypatch):
    """If webhook_url is passed explicitly, it wins over the env var."""
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://env.example/x")
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    fake.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake) as m:
        ok = send_to_slack([_make_alert()],
                           webhook_url="https://explicit.example/y")
    assert ok is True
    # The urlopen must have been called with the explicit URL
    args, kwargs = m.call_args
    req = args[0]
    assert req.full_url == "https://explicit.example/y"


def test_send_to_slack_5xx_returns_false(monkeypatch):
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://hooks.example/x")
    fake = MagicMock()
    fake.__enter__.return_value.status = 503
    fake.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=fake):
        assert send_to_slack([_make_alert()]) is False


def test_send_to_slack_network_error_returns_false(monkeypatch):
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://hooks.example/x")
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("refused")):
        assert send_to_slack([_make_alert()]) is False


# ---------------------------------------------------------------------------
# fire_alerts — pipeline (sent / failed / skipped)
# ---------------------------------------------------------------------------


def test_fire_alerts_empty_returns_empty(tmp_path: Path):
    assert fire_alerts([], history_path=tmp_path / "h.jsonl") == []


def test_fire_alerts_no_webhook_marks_skipped(monkeypatch, tmp_path: Path):
    """No KAIROS_SLACK_WEBHOOK → status='skipped' for every alert."""
    monkeypatch.delenv("KAIROS_SLACK_WEBHOOK", raising=False)
    hist = tmp_path / "h.jsonl"
    fired = fire_alerts([_make_alert(severity="warning"),
                         _make_alert(severity="critical")],
                        history_path=hist)
    assert len(fired) == 2
    assert all(f.status == "skipped" for f in fired)
    assert all(f.channel == "none" for f in fired)
    assert all("KAIROS_SLACK_WEBHOOK" in f.error for f in fired)
    # History must have 2 lines
    lines = [l for l in hist.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 2


def test_fire_alerts_success_marks_sent(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://hooks.example/x")
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    fake.__exit__.return_value = False
    hist = tmp_path / "h.jsonl"
    with patch("urllib.request.urlopen", return_value=fake):
        fired = fire_alerts([_make_alert(severity="warning"),
                             _make_alert(severity="critical",
                                         kind="call_spike")],
                            history_path=hist)
    assert len(fired) == 2
    assert all(f.status == "sent" for f in fired)
    assert all(f.channel == "slack" for f in fired)
    assert all("hooks.example" in f.channel_url for f in fired)
    assert all(f.error == "" for f in fired)
    lines = [l for l in hist.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 2


def test_fire_alerts_4xx_marks_failed(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://hooks.example/x")
    fake = MagicMock()
    fake.__enter__.return_value.status = 404
    fake.__exit__.return_value = False
    hist = tmp_path / "h.jsonl"
    with patch("urllib.request.urlopen", return_value=fake):
        fired = fire_alerts([_make_alert()], history_path=hist)
    assert len(fired) == 1
    assert fired[0].status == "failed"
    assert "non-2xx" in fired[0].error


def test_fire_alerts_explicit_url(monkeypatch, tmp_path: Path):
    """fire_alerts(webhook_url=...) overrides the env var."""
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://env.example/x")
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    fake.__exit__.return_value = False
    hist = tmp_path / "h.jsonl"
    with patch("urllib.request.urlopen", return_value=fake) as m:
        fired = fire_alerts([_make_alert()],
                            webhook_url="https://explicit.example/y",
                            history_path=hist)
    assert fired[0].status == "sent"
    args, _ = m.call_args
    assert args[0].full_url == "https://explicit.example/y"


def test_fire_alerts_unknown_channel_skips(monkeypatch, tmp_path: Path):
    hist = tmp_path / "h.jsonl"
    fired = fire_alerts([_make_alert()], channel="telegram",
                        history_path=hist)
    assert len(fired) == 1
    assert fired[0].status == "skipped"
    assert "unknown channel" in fired[0].error


# ---------------------------------------------------------------------------
# append_to_history / read_history
# ---------------------------------------------------------------------------


def test_history_round_trip(tmp_path: Path):
    p = tmp_path / "h.jsonl"
    f = FiredAlert(
        timestamp=123.0, severity="warning", kind="cost_spike",
        message="m1", metric="cost_usd", baseline=1.0, current=2.0,
        delta_pct=100.0, channel="slack", channel_url="https://h/",
        status="sent", error="",
    )
    append_to_history(f, p)
    g = FiredAlert(
        timestamp=124.0, severity="critical", kind="call_spike",
        message="m2", metric="per_call", baseline=0.01, current=0.05,
        delta_pct=400.0, channel="none", channel_url="",
        status="skipped", error="no webhook",
    )
    append_to_history(g, p)
    out = read_history(p, limit=10)
    # Newest first
    assert len(out) == 2
    assert out[0]["message"] == "m2"
    assert out[1]["message"] == "m1"


def test_history_missing_file_returns_empty(tmp_path: Path):
    assert read_history(tmp_path / "nope.jsonl") == []


def test_history_corrupt_lines_skipped(tmp_path: Path):
    p = tmp_path / "h.jsonl"
    p.write_text(
        json.dumps({"timestamp": 1, "severity": "warning", "kind": "x",
                    "message": "ok", "metric": "cost_usd",
                    "baseline": 1.0, "current": 2.0, "delta_pct": 100.0,
                    "channel": "slack", "channel_url": "u", "status": "sent",
                    "error": ""}) + "\n"
        + "{not json\n"
        + json.dumps({"timestamp": 2, "severity": "critical", "kind": "y",
                      "message": "ok2", "metric": "cost_usd",
                      "baseline": 1.0, "current": 3.0, "delta_pct": 200.0,
                      "channel": "slack", "channel_url": "u",
                      "status": "sent", "error": ""}) + "\n",
        encoding="utf-8",
    )
    out = read_history(p, limit=10)
    # 2 valid + 1 skipped
    assert len(out) == 2
    # Newest first
    assert out[0]["message"] == "ok2"


def test_history_limit_truncates(tmp_path: Path):
    p = tmp_path / "h.jsonl"
    for i in range(5):
        f = FiredAlert(
            timestamp=float(i), severity="info", kind="k",
            message=f"m{i}", metric="x", baseline=0, current=0,
            delta_pct=0, channel="slack", channel_url="u",
            status="sent", error="",
        )
        append_to_history(f, p)
    out = read_history(p, limit=3)
    assert len(out) == 3
    # Newest first
    assert out[0]["message"] == "m4"
    assert out[2]["message"] == "m2"


# ---------------------------------------------------------------------------
# CLI — detect
# ---------------------------------------------------------------------------


def _write_run(p: Path, cases: list) -> None:
    p.write_text(json.dumps({"cases": cases}), encoding="utf-8")


def _cli(argv, monkeypatch=None):
    """Run main(argv) and capture stdout + return code."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def test_cli_detect_no_regressions(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KAIROS_SLACK_WEBHOOK", raising=False)
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write_run(base, [{"name": "a", "cost_usd": 0.01}])
    _write_run(cur, [{"name": "a", "cost_usd": 0.011}])  # +10% — no alert
    rc, out = _cli(["detect", str(base), str(cur)])
    assert rc == 0
    assert "No regressions" in out


def test_cli_detect_warning_no_send(tmp_path: Path, monkeypatch):
    """--no-send must not touch the network."""
    monkeypatch.delenv("KAIROS_SLACK_WEBHOOK", raising=False)
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write_run(base, [{"name": "a", "cost_usd": 0.01}])
    _write_run(cur, [{"name": "a", "cost_usd": 0.02}])  # +100% → warning
    rc, out = _cli(["detect", str(base), str(cur), "--no-send"])
    assert rc == 0
    assert "Found" in out
    assert "no-send" in out


def test_cli_detect_critical_exits_2(tmp_path: Path, monkeypatch):
    """Critical alert → exit 2 (CI can fail on this)."""
    monkeypatch.delenv("KAIROS_SLACK_WEBHOOK", raising=False)
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write_run(base, [{"name": "a", "cost_usd": 0.01}])
    _write_run(cur, [{"name": "a", "cost_usd": 0.05}])  # +400% → critical
    rc, out = _cli(["detect", str(base), str(cur), "--no-send"])
    assert rc == 2
    assert "CRITICAL" in out or "critical" in out


def test_cli_detect_json_no_alerts(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KAIROS_SLACK_WEBHOOK", raising=False)
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write_run(base, [{"name": "a", "cost_usd": 0.01}])
    _write_run(cur, [{"name": "a", "cost_usd": 0.01}])
    rc, out = _cli(["detect", str(base), str(cur), "--json"])
    assert rc == 0
    data = json.loads(out)
    assert data == []


def test_cli_detect_json_critical_exits_1(tmp_path: Path, monkeypatch):
    """--json with a critical alert returns exit code 1."""
    monkeypatch.delenv("KAIROS_SLACK_WEBHOOK", raising=False)
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write_run(base, [{"name": "a", "cost_usd": 0.01}])
    _write_run(cur, [{"name": "a", "cost_usd": 0.05}])
    rc, out = _cli(["detect", str(base), str(cur), "--json"])
    assert rc == 1
    data = json.loads(out)
    assert any(a["severity"] == "critical" for a in data)


def test_cli_detect_dispatches_via_webhook(tmp_path: Path, monkeypatch):
    """With a webhook + a warning, fire_alerts is called and the URL is used."""
    monkeypatch.setenv("KAIROS_SLACK_WEBHOOK", "https://hooks.example/x")
    fake = MagicMock()
    fake.__enter__.return_value.status = 200
    fake.__exit__.return_value = False
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    _write_run(base, [{"name": "a", "cost_usd": 0.01}])
    _write_run(cur, [{"name": "a", "cost_usd": 0.02}])  # +100% warning
    with patch("urllib.request.urlopen", return_value=fake) as m:
        rc, out = _cli(["detect", str(base), str(cur)])
    assert rc == 0  # warning, not critical
    assert "Dispatched" in out
    args, _ = m.call_args
    assert args[0].full_url == "https://hooks.example/x"


# ---------------------------------------------------------------------------
# CLI — history
# ---------------------------------------------------------------------------


def test_cli_history_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    rc, out = _cli(["history"])
    assert rc == 0
    assert "No alerts" in out


def test_cli_history_with_entries(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    p = tmp_path / "alerts.jsonl"
    p.write_text(json.dumps({
        "timestamp": 1700000000, "severity": "warning",
        "kind": "cost_spike", "message": "x", "metric": "cost_usd",
        "baseline": 1.0, "current": 2.0, "delta_pct": 100.0,
        "channel": "slack", "channel_url": "h", "status": "sent",
        "error": "",
    }) + "\n", encoding="utf-8")
    rc, out = _cli(["history"])
    assert rc == 0
    assert "Recent alerts" in out
    assert "WARNING" in out


def test_cli_history_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    p = tmp_path / "alerts.jsonl"
    p.write_text(json.dumps({
        "timestamp": 1, "severity": "info", "kind": "k",
        "message": "m", "metric": "x", "baseline": 0, "current": 0,
        "delta_pct": 0, "channel": "slack", "channel_url": "u",
        "status": "sent", "error": "",
    }) + "\n", encoding="utf-8")
    rc, out = _cli(["history", "--json"])
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["message"] == "m"
