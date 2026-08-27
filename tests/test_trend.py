"""Tests for kairos.trend (Round 24 multi-run aggregator)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kairos.trend import (
    RunPoint,
    TrendReport,
    _load_run,
    _p95,
    aggregate_trend,
    aggregate_trend_from_dir,
)


def _make_run(tmp_path: Path, name: str, pass_rate: float, cost: float = 0.01,
              started: float = 0.0) -> Path:
    """Write a fake run JSON and return its path."""
    cases = []
    n_total = 5
    n_pass = int(n_total * pass_rate)
    for i in range(n_total):
        cases.append({
            "name": f"c{i}",
            "passed": i < n_pass,
            "score": 1.0 if i < n_pass else 0.0,
            "cost_usd": cost / n_total,
            "tokens_in": 10, "tokens_out": 20,
            "duration_ms": 100 + i * 10,
        })
    data = {
        "suite_name": "smoke",
        "run_id": name,
        "cases": cases,
        "started_at": started or time.time(),
        "pass_rate": pass_rate,
    }
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _p95
# ---------------------------------------------------------------------------


def test_p95_empty():
    assert _p95([]) == 0.0


def test_p95_single():
    assert _p95([42.0]) == 42.0


def test_p95_n_less_than_20_returns_max():
    assert _p95([1.0, 2.0, 3.0]) == 3.0


def test_p95_n_geq_20_quantile():
    values = list(range(1, 101))  # 1..100
    p95 = _p95(values)
    # 95th percentile of 1..100 should be ~95-100
    assert 90 <= p95 <= 100


# ---------------------------------------------------------------------------
# _load_run
# ---------------------------------------------------------------------------


def test_load_run_extracts_fields(tmp_path: Path):
    p = _make_run(tmp_path, "run-1.json", pass_rate=0.6, cost=0.05)
    pt = _load_run(p)
    assert pt is not None
    assert pt.suite_name == "smoke"
    assert pt.run_id == "run-1.json"
    assert pt.cases == 5
    assert pt.passed == 3  # 0.6 * 5
    assert pt.pass_rate == 0.6
    assert abs(pt.cost_usd - 0.05) < 1e-9
    assert pt.tokens == 5 * (10 + 20)
    assert pt.avg_duration_ms > 0
    assert pt.p95_duration_ms > 0


def test_load_run_handles_missing_file(tmp_path: Path):
    assert _load_run(tmp_path / "nope.json") is None


def test_load_run_handles_corrupt(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert _load_run(p) is None


def test_load_run_handles_no_cases(tmp_path: Path):
    """A run with no cases returns None (we can't make a point from it)."""
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"cases": []}), encoding="utf-8")
    assert _load_run(p) is None


# ---------------------------------------------------------------------------
# aggregate_trend
# ---------------------------------------------------------------------------


def test_aggregate_trend_with_explicit_files(tmp_path: Path):
    p1 = _make_run(tmp_path, "r1.json", pass_rate=0.5, cost=0.01)
    p2 = _make_run(tmp_path, "r2.json", pass_rate=0.7, cost=0.02)
    p3 = _make_run(tmp_path, "r3.json", pass_rate=0.9, cost=0.03)
    report = aggregate_trend([p1, p2, p3])
    assert report.n_total == 3
    assert report.avg_pass_rate == pytest.approx((0.5 + 0.7 + 0.9) / 3)
    assert report.avg_cost_usd == pytest.approx(0.02)
    # First to last: 0.5 → 0.9 = +40 pp
    assert report.pass_rate_first_to_last == pytest.approx(40.0)
    # First to last cost: 0.01 → 0.03 = +200%
    assert report.cost_first_to_last == pytest.approx(200.0)


def test_aggregate_trend_window_truncates(tmp_path: Path):
    runs = [_make_run(tmp_path, f"r{i}.json", pass_rate=0.5,
                       cost=0.01) for i in range(10)]
    report = aggregate_trend(runs, window=3)
    assert report.n_total == 3
    assert report.runs[0].run_id == "r7.json"
    assert report.runs[-1].run_id == "r9.json"


def test_aggregate_trend_empty_returns_empty_report():
    report = aggregate_trend([])
    assert report.n_total == 0
    assert report.runs == []
    assert report.suite_name == ""


def test_aggregate_trend_from_dir_discovers_runs(tmp_path: Path):
    for i in range(5):
        _make_run(tmp_path, f"run-{i}.json", pass_rate=0.5 + i * 0.1)
    # A non-matching file should be ignored
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")
    report = aggregate_trend_from_dir(tmp_path, pattern="run-*.json")
    assert report.n_total == 5
    # mtime order: r0, r1, ..., r4
    assert report.runs[0].run_id == "run-0.json"
    assert report.runs[-1].run_id == "run-4.json"


def test_aggregate_trend_from_dir_missing_directory(tmp_path: Path):
    report = aggregate_trend_from_dir(tmp_path / "does-not-exist")
    assert report.n_total == 0


def test_aggregate_trend_n_passed_via_real_files(tmp_path: Path):
    # 3 runs: 2 pass, 1 fail
    p_pass1 = _make_run(tmp_path, "r1.json", pass_rate=0.8)
    p_fail = _make_run(tmp_path, "r2.json", pass_rate=0.3)
    p_pass2 = _make_run(tmp_path, "r3.json", pass_rate=0.6)
    report = aggregate_trend([p_pass1, p_fail, p_pass2])
    assert report.n_passed == 2
    assert report.n_total == 3


# ---------------------------------------------------------------------------
# TrendReport serialization
# ---------------------------------------------------------------------------


def test_trend_report_to_dict_round_trip(tmp_path: Path):
    p1 = _make_run(tmp_path, "r1.json", pass_rate=0.5)
    report = aggregate_trend([p1])
    d = report.to_dict()
    assert d["suite_name"] == "smoke"
    assert d["n_total"] == 1
    assert len(d["runs"]) == 1
    # Each run is a dict
    r0 = d["runs"][0]
    assert "name" not in r0  # RunPoint uses 'run_id' (not the skill's name)
    assert r0["run_id"] == "r1.json"
    assert r0["pass_rate"] == 0.5
    # Round-trip via JSON
    text = json.dumps(d)
    restored = json.loads(text)
    assert restored["n_total"] == 1
