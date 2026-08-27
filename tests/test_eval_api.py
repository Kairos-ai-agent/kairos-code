"""Tests for the eval-datasets API (Round 19)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos.eval import CaseResult, SuiteResult


@pytest.fixture
def client_with_datasets(tmp_path: Path):
    """Build a TestClient + 2 fake JSONL datasets in a temp dir."""
    ds_dir = tmp_path / "datasets"
    ds_dir.mkdir()
    # Dataset 1: 2 cases
    (ds_dir / "smoke.jsonl").write_text(
        json.dumps({"case_name": "a", "score": 1.0, "output": "hi",
                    "details": {}}) + "\n" +
        json.dumps({"case_name": "b", "score": 0.5, "output": "bye",
                    "details": {}}) + "\n",
        encoding="utf-8",
    )
    # Dataset 2: 1 case
    (ds_dir / "regression.jsonl").write_text(
        json.dumps({"case_name": "r1", "score": 0.8, "output": "x",
                    "details": {}}) + "\n",
        encoding="utf-8",
    )
    # Empty dataset (no entries)
    (ds_dir / "empty.jsonl").write_text("", encoding="utf-8")

    with patch("kairos.cost._LOG_PATH", tmp_path / "cost.jsonl"):
        from api.routes.cost import router
        app = FastAPI()
        app.include_router(router, prefix="/api/cost")
        with TestClient(app) as c:
            yield c, ds_dir


def test_list_datasets_returns_count_and_size(client_with_datasets):
    client, ds_dir = client_with_datasets
    r = client.get(f"/api/cost/datasets?directory={ds_dir}")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    # Each entry has the expected fields
    for d in body:
        assert "name" in d
        assert "count" in d
        assert "size_bytes" in d
        assert "mtime" in d
        assert "path" in d
    # Counts match what we wrote
    by_name = {d["name"]: d["count"] for d in body}
    assert by_name["smoke.jsonl"] == 2
    assert by_name["regression.jsonl"] == 1
    assert by_name["empty.jsonl"] == 0


def test_list_datasets_empty_directory(tmp_path: Path):
    """No datasets → empty list (not 404)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with patch("kairos.cost._LOG_PATH", tmp_path / "cost.jsonl"):
        from api.routes.cost import router
        app = FastAPI()
        app.include_router(router, prefix="/api/cost")
        with TestClient(app) as c:
            r = c.get(f"/api/cost/datasets?directory={empty}")
            assert r.status_code == 200
            assert r.json() == []


def test_list_datasets_missing_directory(tmp_path: Path):
    """A directory that doesn't exist → empty list, not 500."""
    missing = tmp_path / "does-not-exist"
    with patch("kairos.cost._LOG_PATH", tmp_path / "cost.jsonl"):
        from api.routes.cost import router
        app = FastAPI()
        app.include_router(router, prefix="/api/cost")
        with TestClient(app) as c:
            r = c.get(f"/api/cost/datasets?directory={missing}")
            assert r.status_code == 200
            assert r.json() == []


def test_record_dataset_endpoint(tmp_path: Path):
    """POST /datasets/record appends cases from a run to a dataset."""
    # Build a run with 2 cases (1 pass, 1 fail)
    run = SuiteResult(
        suite_name="t", run_id="r1",
        cases=[
            CaseResult(name="p", score=1.0, passed=True,
                       details={"output": "hello"}),
            CaseResult(name="f", score=0.0, passed=False,
                       details={"output": "bad"}),
        ],
    )
    run_path = tmp_path / "run.json"
    from kairos.eval import save_suite_result
    save_suite_result(run, run_path)
    ds_path = tmp_path / "ds.jsonl"
    with patch("kairos.cost._LOG_PATH", tmp_path / "cost.jsonl"):
        from api.routes.cost import router
        app = FastAPI()
        app.include_router(router, prefix="/api/cost")
        with TestClient(app) as c:
            r = c.post(
                "/api/cost/datasets/record",
                params={"run_path": str(run_path),
                        "dataset_path": str(ds_path),
                        "only_passed": True},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["recorded"] == 1  # only the passing case
    # Verify the file
    lines = ds_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["case_name"] == "p"


def test_record_dataset_endpoint_all_cases(tmp_path: Path):
    """only_passed=False keeps all cases."""
    run = SuiteResult(
        suite_name="t", run_id="r1",
        cases=[
            CaseResult(name="p", score=1.0, passed=True,
                       details={"output": "hi"}),
            CaseResult(name="f", score=0.0, passed=False,
                       details={"output": "bad"}),
        ],
    )
    run_path = tmp_path / "run.json"
    from kairos.eval import save_suite_result
    save_suite_result(run, run_path)
    ds_path = tmp_path / "ds.jsonl"
    with patch("kairos.cost._LOG_PATH", tmp_path / "cost.jsonl"):
        from api.routes.cost import router
        app = FastAPI()
        app.include_router(router, prefix="/api/cost")
        with TestClient(app) as c:
            r = c.post(
                "/api/cost/datasets/record",
                params={"run_path": str(run_path),
                        "dataset_path": str(ds_path),
                        "only_passed": False},
            )
            assert r.json()["recorded"] == 2


def test_derive_from_git_endpoint(tmp_path: Path):
    """POST /datasets/derive scans git log and writes a suite."""
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    os_env = {"HOME": str(home), "USERPROFILE": str(home),
              "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.c",
              "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.c"}
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=os_env)
    subprocess.run(["git", "config", "user.email", "t@t.c"],
                   cwd=repo, check=True, env=os_env)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=repo, check=True, env=os_env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=os_env)
    subprocess.run(
        ["git", "commit", "-q", "-m",
         "kairos: round 1 approved (score 80)\n\n# Plan at this round\n\n"
         "- [x] Init\n"],
        cwd=repo, check=True, env=os_env,
    )
    out = tmp_path / "out.yaml"
    with patch("kairos.cost._LOG_PATH", tmp_path / "cost.jsonl"):
        from api.routes.cost import router
        app = FastAPI()
        app.include_router(router, prefix="/api/cost")
        with TestClient(app) as c:
            r = c.post(
                "/api/cost/datasets/derive",
                params={"repo_path": str(repo), "out_path": str(out),
                        "limit": 10},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["cases"] == 1
    assert out.exists()
    assert "Init" in out.read_text(encoding="utf-8")
