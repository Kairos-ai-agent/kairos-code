"""Prometheus metrics for Kairos.

Exposes a small set of standard metrics + a FastAPI middleware that
records request counts and latencies. The ``/metrics`` endpoint emits
text in the Prometheus exposition format so a real Prometheus server
(or grafana agent) can scrape it directly.

Metrics:

  - ``kairos_http_requests_total{method,path,status}`` counter
  - ``kairos_http_request_duration_seconds{method,path}`` histogram
  - ``kairos_agent_invocations_total{agent,role,status}`` counter
  - ``kairos_agent_invocation_duration_seconds{agent,role}`` histogram
  - ``kairos_agent_tokens_total{agent,role,kind}`` counter
  - ``kairos_loop_rounds_total{outcome}`` counter
  - ``kairos_voice_turns_total{outcome}`` counter
  - ``kairos_mcp_tool_calls_total{server,tool,outcome}`` counter
  - ``kairos_cloud_operations_total{provider,operation,outcome}`` counter
  - ``kairos_sandbox_uses_total{kind,outcome}`` counter
  - ``kairos_active_sessions`` gauge

The metrics module is intentionally a thin wrapper around
``prometheus_client`` — if Prometheus isn't installed, all helpers
become no-ops and the ``/metrics`` endpoint returns a 501 with a
helpful message.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy initialization
# ---------------------------------------------------------------------------


_ENABLED = True
_REGISTRY = None
_COUNTERS: Dict[str, Any] = {}
_HISTOGRAMS: Dict[str, Any] = {}
_GAUGES: Dict[str, Any] = {}


def _try_import():
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    try:
        from prometheus_client import (
            Counter, Histogram, Gauge, CollectorRegistry, REGISTRY,
            generate_latest, CONTENT_TYPE_LATEST,
        )
    except ImportError:
        return None
    # Build a dedicated registry so tests can reset it without
    # touching the global default registry.
    _REGISTRY = CollectorRegistry()
    _bootstrap(_REGISTRY)
    return _REGISTRY


def _bootstrap(registry) -> None:
    from prometheus_client import Counter, Histogram, Gauge

    _COUNTERS["http_requests_total"] = Counter(
        "kairos_http_requests_total",
        "Total HTTP requests handled by the Kairos API.",
        ["method", "path", "status"],
        registry=registry,
    )
    _HISTOGRAMS["http_request_duration_seconds"] = Histogram(
        "kairos_http_request_duration_seconds",
        "HTTP request latency in seconds.",
        ["method", "path"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        registry=registry,
    )
    _COUNTERS["agent_invocations_total"] = Counter(
        "kairos_agent_invocations_total",
        "Total agent invocations.",
        ["agent", "role", "status"],
        registry=registry,
    )
    _HISTOGRAMS["agent_invocation_duration_seconds"] = Histogram(
        "kairos_agent_invocation_duration_seconds",
        "Agent invocation latency in seconds.",
        ["agent", "role"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
        registry=registry,
    )
    _COUNTERS["agent_tokens_total"] = Counter(
        "kairos_agent_tokens_total",
        "Total tokens consumed by agents.",
        ["agent", "role", "kind"],  # kind: input / output
        registry=registry,
    )
    _COUNTERS["loop_rounds_total"] = Counter(
        "kairos_loop_rounds_total",
        "Total Coder/Reviewer loop rounds by outcome.",
        ["outcome"],  # approved, rejected, max_rounds, error
        registry=registry,
    )
    _COUNTERS["voice_turns_total"] = Counter(
        "kairos_voice_turns_total",
        "Total voice turn outcomes.",
        ["outcome"],  # ok, stt_error, llm_error, tts_error
        registry=registry,
    )
    _COUNTERS["mcp_tool_calls_total"] = Counter(
        "kairos_mcp_tool_calls_total",
        "Total MCP tool calls.",
        ["server", "tool", "outcome"],
        registry=registry,
    )
    _COUNTERS["cloud_operations_total"] = Counter(
        "kairos_cloud_operations_total",
        "Total cloud provider operations.",
        ["provider", "operation", "outcome"],
        registry=registry,
    )
    _COUNTERS["sandbox_uses_total"] = Counter(
        "kairos_sandbox_uses_total",
        "Total sandbox activations.",
        ["kind", "outcome"],
        registry=registry,
    )
    _GAUGES["active_sessions"] = Gauge(
        "kairos_active_sessions",
        "Number of active loop/voice sessions currently in memory.",
        registry=registry,
    )


def is_enabled() -> bool:
    return _try_import() is not None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def record_http(method: str, path: str, status: int, duration_s: float) -> None:
    reg = _try_import()
    if reg is None:
        return
    status_s = str(int(status))
    _COUNTERS["http_requests_total"].labels(method=method, path=path, status=status_s).inc()
    _HISTOGRAMS["http_request_duration_seconds"].labels(method=method, path=path).observe(duration_s)


def record_agent_invocation(agent: str, role: str, status: str, duration_s: float) -> None:
    reg = _try_import()
    if reg is None:
        return
    _COUNTERS["agent_invocations_total"].labels(agent=agent, role=role, status=status).inc()
    _HISTOGRAMS["agent_invocation_duration_seconds"].labels(agent=agent, role=role).observe(duration_s)


def record_agent_tokens(agent: str, role: str, kind: str, count: int) -> None:
    reg = _try_import()
    if reg is None or count <= 0:
        return
    _COUNTERS["agent_tokens_total"].labels(agent=agent, role=role, kind=kind).inc(count)


def record_loop_round(outcome: str) -> None:
    reg = _try_import()
    if reg is None:
        return
    _COUNTERS["loop_rounds_total"].labels(outcome=outcome).inc()


def record_voice_turn(outcome: str) -> None:
    reg = _try_import()
    if reg is None:
        return
    _COUNTERS["voice_turns_total"].labels(outcome=outcome).inc()


def record_mcp_tool_call(server: str, tool: str, outcome: str) -> None:
    reg = _try_import()
    if reg is None:
        return
    _COUNTERS["mcp_tool_calls_total"].labels(server=server, tool=tool, outcome=outcome).inc()


def record_cloud_operation(provider: str, operation: str, outcome: str) -> None:
    reg = _try_import()
    if reg is None:
        return
    _COUNTERS["cloud_operations_total"].labels(provider=provider, operation=operation, outcome=outcome).inc()


def record_sandbox_use(kind: str, outcome: str) -> None:
    reg = _try_import()
    if reg is None:
        return
    _COUNTERS["sandbox_uses_total"].labels(kind=kind, outcome=outcome).inc()


def set_active_sessions(count: int) -> None:
    reg = _try_import()
    if reg is None:
        return
    _GAUGES["active_sessions"].set(count)


# ---------------------------------------------------------------------------
# Context manager for timed agent invocations
# ---------------------------------------------------------------------------


@contextmanager
def agent_invocation_timer(agent: str, role: str) -> Iterator[Dict[str, Any]]:
    """Time an agent invocation and record the result.

    Usage::

        with agent_invocation_timer("coder", "code") as ctx:
            result = do_thing()
            ctx["status"] = "ok" if result else "error"
    """
    reg = _try_import()
    state: Dict[str, Any] = {"status": "ok"}
    if reg is None:
        yield state
        return
    start = time.perf_counter()
    try:
        yield state
    except Exception:
        state["status"] = "error"
        raise
    finally:
        duration = time.perf_counter() - start
        record_agent_invocation(agent=agent, role=role,
                                status=state["status"], duration_s=duration)


def render_latest() -> Optional[bytes]:
    """Return the current metrics snapshot in Prometheus text format.

    Returns None if the metrics subsystem isn't available.
    """
    reg = _try_import()
    if reg is None:
        return None
    from prometheus_client import generate_latest
    return generate_latest(reg)


def content_type() -> str:
    """Return the canonical Prometheus content-type string."""
    return "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------


def install_middleware(app, *, path_template: Optional[Any] = None) -> None:
    """Install a request-timing middleware on a FastAPI app.

    ``path_template`` is an optional callable used to collapse dynamic
    paths (``/api/projects/{id}``) into a stable label so the
    cardinality of the metrics stays bounded. Defaults to
    ``app.url_path_for`` when available, else a best-effort regex.
    """
    import time as _time

    def _resolve_template(request) -> str:
        if path_template is not None:
            try:
                return path_template(request)
            except Exception:
                pass
        # Try FastAPI's route matching.
        try:
            route = request.scope.get("route")
            if route is not None and getattr(route, "path", None):
                return route.path
        except Exception:
            pass
        return request.url.path

    @app.middleware("http")
    async def _metrics_middleware(request, call_next):
        reg = _try_import()
        if reg is None:
            return await call_next(request)
        method = request.method
        path = _resolve_template(request)
        start = _time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration = _time.perf_counter() - start
            status = getattr(response, "status_code", 0)
            record_http(method=method, path=path, status=int(status), duration_s=duration)


def install_metrics_endpoint(app, *, path: str = "/metrics") -> None:
    """Add a GET /metrics endpoint that returns Prometheus text format."""
    from fastapi import Response

    @app.get(path, include_in_schema=False)
    def _metrics():
        body = render_latest()
        if body is None:
            return Response(
                content=b"# metrics subsystem unavailable: install prometheus_client\n",
                media_type="text/plain",
                status_code=501,
            )
        return Response(content=body, media_type=content_type())
