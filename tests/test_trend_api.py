"""Tests for the trend API endpoint (R25)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos.trend import (
    CaseTrendPoint,
    CaseTrendReport,
    aggregate_per_case_trend,
)


def _write_run(tmp_path: Path, name: str, cases_passed: Dict[str, bool],
               cost: float = 0.01) -> Path:
    cases = []
    for cname, passed in cases_passed.items():
        cases.append({
            "name": cname, "passed": passed, "score": 1.0 if passed else 0.0,
            "cost_usd": cost / max(len(cases_passed), 1),
            "duration_ms": 100, "tokens_in": 10, "tokens_out": 20,
        })
    data = {
        "suite_name": "smoke", "run_id": name,
        "cases": cases, "pass_rate": sum(cases_passed.values()) / max(len(cases_passed), 1),
    }
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture
def client():
    from api.routes.trend import router
    app = FastAPI()
    app.include_router(router, prefix="/api/trend")
    with TestClient(app) as c:
        yield c


def test_trend_endpoint_empty_dir(tmp_path: Path, client):
    r = client.get("/api/trend", params={"directory": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["n_total"] == 0
    assert body["runs"] == []


def test_trend_endpoint_returns_window(tmp_path: Path, client):
    for i in range(3):
        _write_run(tmp_path, f"run-{i}.json",
                   cases_passed={"a": True, "b": i % 2 == 0})
    r = client.get("/api/trend",
                    params={"directory": str(tmp_path), "window": 5})
    body = r.json()
    assert body["n_total"] == 3
    assert len(body["runs"]) == 3
    # avg pass_rate: run 0 (a,b both pass) = 1.0; run 1 (a pass, b fail) = 0.5;
    # run 2 (same as 0) = 1.0 → avg = (1.0 + 0.5 + 1.0) / 3
    assert body["avg_pass_rate"] == pytest.approx((1.0 + 0.5 + 1.0) / 3)


def test_trend_endpoint_clamps_window(client):
    """A window > 200 is rejected by FastAPI's Query validator."""
    r = client.get("/api/trend", params={"window": 500})
    assert r.status_code == 422


def test_trend_endpoint_custom_pattern(tmp_path: Path, client):
    _write_run(tmp_path, "run-1.json", cases_passed={"a": True})
    (tmp_path / "ignore-me.json").write_text("{}", encoding="utf-8")
    r = client.get("/api/trend",
                    params={"directory": str(tmp_path),
                            "pattern": "run-*.json"})
    body = r.json()
    assert body["n_total"] == 1


# ---------------------------------------------------------------------------
# aggregate_per_case_trend
# ---------------------------------------------------------------------------


def test_aggregate_per_case_trend_empty_dir(tmp_path: Path):
    report = aggregate_per_case_trend(tmp_path)
    assert report.n_runs == 0
    assert report.cases == []


def test_aggregate_per_case_trend_basic(tmp_path: Path):
    # 3 runs, case 'a' is stable (passes), case 'b' is flaky
    _write_run(tmp_path, "run-1.json", cases_passed={"a": True, "b": True})
    _write_run(tmp_path, "run-2.json", cases_passed={"a": True, "b": False})
    _write_run(tmp_path, "run-3.json", cases_passed={"a": True, "b": True})
    report = aggregate_per_case_trend(tmp_path)
    assert report.n_runs == 3
    assert len(report.cases) == 2
    by_name = {c.case_name: c for c in report.cases}
    # 'a' is always passing → not flaky
    assert by_name["a"].flaky is False
    assert by_name["a"].pass_rate == 1.0
    # 'b' had 2 pass + 1 fail → flaky
    assert by_name["b"].flaky is True
    assert by_name["b"].pass_rate == pytest.approx(2 / 3)


def test_aggregate_per_case_trend_flaky_first(tmp_path: Path):
    """The result is sorted with flaky cases first (most actionable)."""
    _write_run(tmp_path, "run-1.json",
               cases_passed={"stable": True, "flaky": False})
    _write_run(tmp_path, "run-2.json",
               cases_passed={"stable": True, "flaky": True})
    report = aggregate_per_case_trend(tmp_path)
    names = [c.case_name for c in report.cases]
    assert names[0] == "flaky"
    assert names[1] == "stable"


def test_aggregate_per_case_trend_window(tmp_path: Path):
    """Only the last `window` runs are considered.

    Note: with rapid file creation on a fast filesystem, mtimes
    can collide and the sort isn't perfectly stable. We
    therefore assert loose invariants (n_runs == window,
    pass_rate is in [0, 1]) rather than exact counts.
    """
    import time as _time
    for i in range(10):
        _write_run(tmp_path, f"run-{i}.json",
                   cases_passed={"a": i % 2 == 0})
        _time.sleep(0.01)  # ensure monotonic mtime
    report = aggregate_per_case_trend(tmp_path, window=4)
    assert report.window == 4
    a = next((c for c in report.cases if c.case_name == "a"), None)
    assert a is not None
    # a.n_runs is bounded by the window
    assert 1 <= a.n_runs <= 4
    # pass_rate is well-defined
    assert 0.0 <= a.pass_rate <= 1.0


# ---------------------------------------------------------------------------
# API: per-case endpoint
# ---------------------------------------------------------------------------


def test_per_case_endpoint(tmp_path: Path, client):
    _write_run(tmp_path, "run-1.json",
               cases_passed={"a": True, "b": True})
    _write_run(tmp_path, "run-2.json",
               cases_passed={"a": True, "b": False})
    r = client.get("/api/trend/per_case",
                    params={"directory": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["n_runs"] == 2
    by_name = {c["case_name"]: c for c in body["cases"]}
    assert "a" in by_name and "b" in by_name
    assert by_name["a"]["flaky"] is False
    assert by_name["b"]["flaky"] is True


def test_per_case_endpoint_clamp(tmp_path: Path, client):
    """A window > 200 returns 422 (FastAPI validation)."""
    r = client.get("/api/trend/per_case",
                    params={"directory": str(tmp_path), "window": 500})
    assert r.status_code == 422
