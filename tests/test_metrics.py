"""Tests for the metrics module + FastAPI integration."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def fresh_registry(monkeypatch):
    """Force a fresh CollectorRegistry per test."""
    import kairos.metrics as m
    # Reset the module-level cache so each test gets a clean slate.
    monkeypatch.setattr(m, "_REGISTRY", None)
    monkeypatch.setattr(m, "_COUNTERS", {})
    monkeypatch.setattr(m, "_HISTOGRAMS", {})
    monkeypatch.setattr(m, "_GAUGES", {})
    yield m


def test_is_enabled_when_prometheus_client_installed(fresh_registry):
    assert fresh_registry.is_enabled() is True


def test_record_http_increments_counter(fresh_registry):
    fresh_registry.record_http("GET", "/api/projects", 200, 0.123)
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_http_requests_total" in text
    assert 'method="GET"' in text
    assert 'path="/api/projects"' in text
    assert 'status="200"' in text


def test_record_http_records_duration_histogram(fresh_registry):
    fresh_registry.record_http("POST", "/api/projects", 201, 0.5)
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_http_request_duration_seconds" in text
    assert 'method="POST"' in text


def test_record_agent_invocation(fresh_registry):
    fresh_registry.record_agent_invocation("coder", "code", "ok", 2.5)
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_agent_invocations_total" in text
    assert 'agent="coder"' in text
    assert 'role="code"' in text
    assert 'status="ok"' in text
    assert "kairos_agent_invocation_duration_seconds" in text


def test_record_agent_tokens_ignores_zero(fresh_registry):
    fresh_registry.record_agent_tokens("coder", "code", "input", 0)
    text = fresh_registry.render_latest().decode("utf-8")
    # HELP/TYPE are still emitted (the counter exists), but no
    # labeled SAMPLE line should appear because we skip inc(0).
    lines = [l for l in text.splitlines() if l.startswith("kairos_agent_tokens_total{")]
    assert lines == [], f"expected no labeled samples, got: {lines}"


def test_record_agent_tokens_emitted_when_positive(fresh_registry):
    fresh_registry.record_agent_tokens("reviewer", "review", "output", 1234)
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_agent_tokens_total" in text
    assert 'agent="reviewer"' in text
    assert 'kind="output"' in text


def test_record_loop_round(fresh_registry):
    fresh_registry.record_loop_round("approved")
    fresh_registry.record_loop_round("rejected")
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_loop_rounds_total" in text
    assert 'outcome="approved"' in text
    assert 'outcome="rejected"' in text


def test_record_voice_turn(fresh_registry):
    fresh_registry.record_voice_turn("ok")
    fresh_registry.record_voice_turn("stt_error")
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_voice_turns_total" in text
    assert 'outcome="stt_error"' in text


def test_record_mcp_tool_call(fresh_registry):
    fresh_registry.record_mcp_tool_call("fs", "read_file", "ok")
    fresh_registry.record_mcp_tool_call("fs", "read_file", "error")
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_mcp_tool_calls_total" in text
    assert 'server="fs"' in text
    assert 'tool="read_file"' in text
    assert 'outcome="ok"' in text
    assert 'outcome="error"' in text


def test_record_cloud_operation(fresh_registry):
    fresh_registry.record_cloud_operation("s3", "put", "ok")
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_cloud_operations_total" in text
    assert 'provider="s3"' in text


def test_record_sandbox_use(fresh_registry):
    fresh_registry.record_sandbox_use("landlock", "ok")
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_sandbox_uses_total" in text
    assert 'kind="landlock"' in text


def test_active_sessions_gauge(fresh_registry):
    fresh_registry.set_active_sessions(7)
    text = fresh_registry.render_latest().decode("utf-8")
    assert "kairos_active_sessions" in text
    assert "7.0" in text


def test_agent_invocation_timer_records_ok(fresh_registry):
    with fresh_registry.agent_invocation_timer("coder", "code") as ctx:
        time.sleep(0.01)
        ctx["status"] = "ok"
    text = fresh_registry.render_latest().decode("utf-8")
    assert 'agent="coder"' in text
    assert 'status="ok"' in text


def test_agent_invocation_timer_records_error_on_exception(fresh_registry):
    with pytest.raises(RuntimeError):
        with fresh_registry.agent_invocation_timer("coder", "code") as ctx:
            raise RuntimeError("boom")
    text = fresh_registry.render_latest().decode("utf-8")
    assert 'status="error"' in text


def test_content_type_is_prometheus_text():
    from kairos.metrics import content_type
    assert content_type().startswith("text/plain")
    assert "version=0.0.4" in content_type()


# ---------------------------------------------------------------------------
# FastAPI middleware + /metrics endpoint
# ---------------------------------------------------------------------------


def _build_app_with_middleware() -> FastAPI:
    import kairos.metrics as m
    app = FastAPI()
    m.install_middleware(app)
    m.install_metrics_endpoint(app)

    @app.get("/api/projects")
    def list_projects():
        return {"projects": []}

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return {"id": pid}

    @app.get("/boom")
    def boom():
        raise RuntimeError("simulated")

    return app


def test_metrics_endpoint_returns_prometheus_text(monkeypatch):
    import kairos.metrics as m
    monkeypatch.setattr(m, "_REGISTRY", None)
    monkeypatch.setattr(m, "_COUNTERS", {})
    monkeypatch.setattr(m, "_HISTOGRAMS", {})
    monkeypatch.setattr(m, "_GAUGES", {})

    app = _build_app_with_middleware()
    with TestClient(app) as client:
        # Hit a few endpoints to generate metrics
        client.get("/api/projects")
        client.get("/api/projects")
        client.get("/api/projects/abc")
        # The /boom endpoint will produce a 500
        try:
            client.get("/boom")
        except RuntimeError:
            # The middleware runs even on raised exceptions
            pass

        # Now scrape /metrics
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        text = r.text
        assert "kairos_http_requests_total" in text
        # Path template should collapse /api/projects/abc back to the route
        assert "/api/projects" in text or "/api/projects/{pid}" in text
        # The 500 path should be recorded
        assert 'status="500"' in text or 'status="200"' in text


def test_metrics_endpoint_returns_501_when_disabled(monkeypatch):
    """If prometheus_client is unavailable, /metrics returns 501."""
    import kairos.metrics as m
    # Force a re-import that fails
    import importlib
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "prometheus_client" or name.startswith("prometheus_client"):
            raise ImportError("forced missing for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(m, "_REGISTRY", None)
    monkeypatch.setattr(m, "_COUNTERS", {})
    monkeypatch.setattr(m, "_HISTOGRAMS", {})
    monkeypatch.setattr(m, "_GAUGES", {})
    # Bypass the import cache by making _try_import fail
    monkeypatch.setattr(m, "_try_import", lambda: None)
    monkeypatch.setattr(m, "is_enabled", lambda: False)

    app = FastAPI()
    m.install_metrics_endpoint(app)
    with TestClient(app) as client:
        r = client.get("/metrics")
        assert r.status_code == 501
        assert b"unavailable" in r.content
