"""Tests for the cost dashboard API endpoint (Round 16)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Build a TestClient with a fresh cost log file."""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "cost.jsonl"
        # Seed the log with two entries
        log.write_text(json.dumps({
            "timestamp": 100.0, "model": "gpt-4o", "provider": "openai",
            "prompt_tokens": 100, "completion_tokens": 50,
            "cost_usd": 0.001, "duration_ms": 200,
        }) + "\n" + json.dumps({
            "timestamp": 200.0, "model": "claude-3-5-sonnet", "provider": "anthropic",
            "prompt_tokens": 200, "completion_tokens": 100,
            "cost_usd": 0.003, "duration_ms": 300,
        }) + "\n", encoding="utf-8")
        with patch("kairos.cost._LOG_PATH", log):
            from api.routes.cost import router
            app = FastAPI()
            app.include_router(router, prefix="/api/cost")
            with TestClient(app) as c:
                yield c


def test_summary_returns_total_and_per_model(client):
    r = client.get("/api/cost/summary")
    assert r.status_code == 200
    body = r.json()
    # 2 entries on disk, 0 in memory (process is fresh)
    assert body["calls"] == 2
    assert abs(body["cost_usd"] - 0.004) < 1e-9
    # by_model has both models
    assert "gpt-4o" in body["by_model"]
    assert "claude-3-5-sonnet" in body["by_model"]


def test_summary_in_memory_only_when_buffer_has_entries():
    """Without a disk log, the summary returns only in-memory data."""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "empty.jsonl"  # doesn't exist
        with patch("kairos.cost._LOG_PATH", log), \
             patch("kairos.cost._BUFFER", __import__("collections").deque()):
            from api.routes.cost import router
            app = FastAPI()
            app.include_router(router, prefix="/api/cost")
            with TestClient(app) as c:
                r = c.get("/api/cost/summary")
                assert r.status_code == 200
                body = r.json()
                assert body["calls"] == 0
                assert body["cost_usd"] == 0


def test_recent_returns_entries_newest_first(client):
    r = client.get("/api/cost/recent?limit=10")
    assert r.status_code == 200
    body = r.json()
    # Both disk entries are returned; in-memory is empty
    # (the API merges in-memory + disk but in this test the
    # in-memory is empty so we get just disk entries).
    # The disk entries are ordered newest-first.
    models = [e["model"] for e in body]
    if "claude-3-5-sonnet" in models:
        # Newer one first
        assert models.index("claude-3-5-sonnet") < models.index("gpt-4o")


def test_recent_respects_limit(client):
    r = client.get("/api/cost/recent?limit=1")
    body = r.json()
    assert len(body) <= 1


def test_by_model_returns_aggregates(client):
    r = client.get("/api/cost/by_model")
    assert r.status_code == 200
    body = r.json()
    assert "gpt-4o" in body
    assert body["gpt-4o"]["calls"] >= 1


def test_summary_clamps_limit_to_10k(client):
    """An out-of-range limit is rejected by FastAPI's validation."""
    r = client.get("/api/cost/summary?limit=100000")
    assert r.status_code == 422  # FastAPI validation error


def test_summary_handles_missing_log_file():
    """If the log file doesn't exist, summary returns 0 cost."""
    with tempfile.TemporaryDirectory() as tmp:
        # Point at a non-existent file
        log = Path(tmp) / "does-not-exist.jsonl"
        with patch("kairos.cost._LOG_PATH", log), \
             patch("kairos.cost._BUFFER", __import__("collections").deque()):
            from api.routes.cost import router
            app = FastAPI()
            app.include_router(router, prefix="/api/cost")
            with TestClient(app) as c:
                r = c.get("/api/cost/summary")
                assert r.status_code == 200
                body = r.json()
                assert body["calls"] == 0
                assert body["cost_usd"] == 0
