"""Tests for the skill search API endpoint (R23)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Build a TestClient with the skill_search router."""
    from api.routes.skill_search import router
    app = FastAPI()
    app.include_router(router, prefix="/api/skill_search")
    with TestClient(app) as c:
        yield c


def test_search_returns_results_for_known_skill(client):
    """The bundled superpowers skills are searchable."""
    r = client.get("/api/skill_search/search", params={"q": "test"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "test"
    # At least one skill matches "test" (e.g. test-driven-development)
    names = [r["name"] for r in body["results"]]
    assert any("test" in n.lower() for n in names), (
        f"no 'test' in {[n for n in names]}"
    )


def test_search_respects_limit(client):
    r = client.get("/api/skill_search/search",
                    params={"q": "the", "limit": 3})
    body = r.json()
    assert len(body["results"]) <= 3


def test_search_with_no_results_returns_empty_list(client):
    r = client.get("/api/skill_search/search",
                    params={"q": "xyzzy_no_match_xyzzy"})
    body = r.json()
    assert body["count"] == 0
    assert body["results"] == []


def test_search_rejects_empty_query(client):
    r = client.get("/api/skill_search/search", params={"q": ""})
    # FastAPI Query validation: min_length=1 → 422
    assert r.status_code == 422


def test_search_rejects_limit_out_of_range(client):
    r = client.get("/api/skill_search/search",
                    params={"q": "x", "limit": 1000})
    assert r.status_code == 422  # max 50


def test_reindex_endpoint_resets_cache(client):
    r = client.post("/api/skill_search/reindex")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reindex requested"


def test_search_returns_snippet_in_results(client):
    r = client.get("/api/skill_search/search",
                    params={"q": "test", "limit": 1})
    body = r.json()
    if body["results"]:
        first = body["results"][0]
        # The result shape matches the kairos.skill_search contract
        assert "name" in first
        assert "source_path" in first
        assert "priority" in first
        assert "score" in first
        assert "snippet" in first
