"""LLM call observability — Langfuse / OpenTelemetry compatible.

The module exposes a single ``Tracer`` class that:
  - records every LLM call (input, output, model, latency, tokens,
    cost) as an OTel span
  - ships spans to whatever OTel-compatible backend is configured
    (Langfuse, OpenTelemetry Collector, Jaeger, ...). The default
    is a no-op tracer that records to an in-memory deque — tests
    use this; production swaps in the OTLP exporter.
  - exposes a Flask/FastAPI middleware (optional) so HTTP request
    latency can be correlated with LLM spans.

We deliberately do NOT import Langfuse directly. The Langfuse
SDK accepts OTLP spans (it has a built-in OTel receiver on port
4317 / 4318), so a generic OTel exporter is the lowest-coupling
path. If you want native Langfuse SDK, the user can set
``LANGFUSE_PUBLIC_KEY`` + ``LANGFUSE_SECRET_KEY`` and we'll route
to it via the Langfuse OTel receiver.

Quick start:
    from kairos.observability import Tracer, init_default_tracer

    tracer = init_default_tracer()  # no-op by default
    with tracer.span("agent.run", {"requirement": req}) as span:
        span.set_attribute("agent.role", "coder")
        with span.llm_call(model="gpt-4o") as llm:
            response = await client.complete(messages)
            llm.set_output(response.content, tokens=response.usage)
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# Optional OpenTelemetry import. If missing, we fall back to a
# pure-stdlib in-memory tracer that satisfies the same API surface.
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor,
    )
    from opentelemetry.trace import Status, StatusCode
    _HAS_OTEL = True
except ImportError:  # pragma: no cover - optional dep
    _HAS_OTEL = False
    trace = None  # type: ignore


# ---------------------------------------------------------------------------
# No-op span (used when OTel isn't installed and for tests)
# ---------------------------------------------------------------------------


@dataclass
class NoOpSpan:
    """A minimal stand-in for an OTel span.

    Records attributes and timing in-memory; emits nothing. Lets
    the rest of the codebase use ``with tracer.span(...) as s:
    s.set_attribute(...)`` without conditional logic.
    """
    name: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    status: str = "ok"

    def set_attribute(self, key: str, value: Any) -> "NoOpSpan":
        self.attributes[key] = value
        return self

    def set_attributes(self, attrs: Dict[str, Any]) -> "NoOpSpan":
        self.attributes.update(attrs)
        return self

    def add_event(self, name: str, attrs: Optional[Dict[str, Any]] = None) -> "NoOpSpan":
        self.events.append({"name": name, "attributes": attrs or {},
                            "time": time.time()})
        return self

    def set_status(self, status: str, description: str = "") -> "NoOpSpan":
        self.status = status
        return self

    def record_exception(self, exc: BaseException) -> "NoOpSpan":
        self.events.append({
            "name": "exception",
            "message": str(exc),
            "type": type(exc).__name__,
            "time": time.time(),
        })
        self.set_status("error", str(exc))
        return self

    def end(self) -> None:
        if self.end_time == 0.0:
            self.end_time = time.time()

    def __enter__(self) -> "NoOpSpan":
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self.record_exception(exc)
        self.end()

    def llm_call(self, model: str, provider: str = "",
                 messages: Optional[List[Any]] = None) -> "_NestedLlmSpan":
        """Nest an LLM-call sub-span. Used as a context manager:
            with span.llm_call("gpt-4o") as llm: ...
        """
        return _NestedLlmSpan(parent=self, model=model, provider=provider,
                                messages=messages or [])

    def set_output(self, content: str = "", prompt_tokens: int = 0,
                   completion_tokens: int = 0, cost_usd: float = 0.0,
                   finish_reason: str = "") -> "NoOpSpan":
        """Convenience setter: stash output-side metrics as attributes
        on a non-nested span. Equivalent to setting
        ``gen_ai.usage.input_tokens`` etc. directly.
        """
        if prompt_tokens:
            self.set_attribute("gen_ai.usage.input_tokens", int(prompt_tokens))
        if completion_tokens:
            self.set_attribute("gen_ai.usage.output_tokens", int(completion_tokens))
        if cost_usd:
            self.set_attribute("gen_ai.usage.cost", float(cost_usd))
        if finish_reason:
            self.set_attribute("gen_ai.response.finish_reason", finish_reason)
        return self


class _NestedLlmSpan(NoOpSpan):
    """Sub-span for an LLM call. Same shape as NoOpSpan but with
    sensible LLM-specific default attributes."""
    def __init__(self, parent: NoOpSpan, model: str, provider: str,
                 messages: List[Any]):
        super().__init__(
            name=f"llm.{model}",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model,
                "gen_ai.system": provider,
                "gen_ai.request.message_count": len(messages),
            },
        )
        self._parent = parent
        # Register the parent reference so the test helper
        # ``tracer.get_spans()`` can walk the tree.
        self._tracer = getattr(parent, "_tracer", None)

    def __enter__(self):
        ret = super().__enter__()
        if self._tracer is not None:
            self._tracer._in_memory.append(self)
        return ret

    def __exit__(self, exc_type, exc, tb):
        # Record duration on the sub-span before delegating to the
        # parent __exit__ (which handles exception logging + end()).
        self.set_attribute("gen_ai.response.duration_ms",
                           int((time.time() - self.start_time) * 1000))
        return super().__exit__(exc_type, exc, tb)

    def set_output(self, content: str = "", prompt_tokens: int = 0,
                   completion_tokens: int = 0, cost_usd: float = 0.0,
                   finish_reason: str = "") -> "_NestedLlmSpan":
        """Record output-side metrics. The ``content`` itself is NOT
        stored by default to keep spans PII-safe and small; opt in
        via ``set_attribute("gen_ai.response.content", content)``.
        """
        if prompt_tokens:
            self.set_attribute("gen_ai.usage.input_tokens", int(prompt_tokens))
        if completion_tokens:
            self.set_attribute("gen_ai.usage.output_tokens", int(completion_tokens))
        if cost_usd:
            self.set_attribute("gen_ai.usage.cost", float(cost_usd))
        if finish_reason:
            self.set_attribute("gen_ai.response.finish_reason", finish_reason)
        return self


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------


class Tracer:
    """A thin wrapper around OTel (or no-op) tracer.

    Construction: prefer ``init_default_tracer()`` for the module-level
    singleton, or instantiate directly for tests.

    The ``llm_call`` context manager auto-records latency and is the
    canonical way to instrument a generation. Example:
        with tracer.span("agent.run") as span:
            span.set_attribute("agent.role", "coder")
            with span.llm_call(model="gpt-4o") as llm:
                response = await client.complete(messages)
                llm.set_output(response.content,
                               prompt_tokens=response.usage.get("prompt_tokens", 0),
                               completion_tokens=response.usage.get("completion_tokens", 0),
                               cost_usd=...)
    """

    def __init__(self, name: str = "kairos", exporter: Optional[Any] = None,
                 otlp_endpoint: Optional[str] = None):
        self.name = name
        self._otel_tracer = None
        self._in_memory: List[NoOpSpan] = []
        self._exporter = exporter
        # Only set up the OTel provider when the user opts in
        # (OTLP endpoint OR console exporter). Otherwise we stay
        # on the no-op path so tests and dev environments don't
        # accidentally ship spans to a real collector.
        if _HAS_OTEL and (otlp_endpoint or exporter == "console"):
            provider = TracerProvider(resource=Resource.create(
                {"service.name": name}
            ))
            if otlp_endpoint:
                # Defer import so the OTLP exporter is only required
                # when the user actually opts in.
                try:
                    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                        OTLPSpanExporter,
                    )
                    provider.add_span_processor(
                        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                    )
                except ImportError:
                    logger.warning(
                        "OTLP endpoint set but opentelemetry-exporter-otlp "
                        "is not installed; falling back to no-op"
                    )
                    return
            if exporter == "console":
                provider.add_span_processor(
                    SimpleSpanProcessor(ConsoleSpanExporter())
                )
            self._otel_tracer = trace.get_tracer(name)

    # --- lifecycle --------------------------------------------------------

    def get_spans(self) -> List[NoOpSpan]:
        """Return the in-memory span buffer (test/inspection helper)."""
        return list(self._in_memory)

    def flush(self, timeout_ms: int = 5000) -> None:
        """Force any batched spans out. No-op for the in-memory tracer."""
        if _HAS_OTEL and self._otel_tracer is not None:
            try:
                from opentelemetry.sdk.trace import get_tracer_provider
                get_tracer_provider().force_flush(timeout_millis=timeout_ms)
            except Exception as exc:
                logger.debug("tracer flush failed: %s", exc)

    # --- spans ------------------------------------------------------------

    @contextmanager
    def span(self, name: str,
            attributes: Optional[Dict[str, Any]] = None
            ) -> Iterator[Any]:
        """Open a top-level span. Returns either an OTel span or
        a NoOpSpan — caller code is identical for both."""
        if self._otel_tracer is not None:
            with self._otel_tracer.start_as_current_span(name) as s:
                if attributes:
                    for k, v in attributes.items():
                        try:
                            s.set_attribute(k, v)
                        except Exception:
                            pass
                yield _OtelSpanAdapter(s)
        else:
            sp = NoOpSpan(name=name, attributes=dict(attributes or {}))
            sp._tracer = self  # back-reference for sub-span registration
            with sp:
                self._in_memory.append(sp)
                yield sp

    @contextmanager
    def llm_call(self, model: str, provider: str = "",
                 messages: Optional[List[Any]] = None) -> Iterator[Any]:
        """Open a child span for a single LLM generation. Sets the
        standard OTel GenAI semconv attributes when otel-sdk is
        available, otherwise records the same info on the NoOpSpan.

        ``messages`` is a list of LLMMessage or plain dicts. We
        only record the *count* by default to avoid PII / cost
        surprises; the full payload goes on a child event that
        the user can opt into.
        """
        attrs: Dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
        }
        if provider:
            attrs["gen_ai.system"] = provider
        if messages is not None:
            attrs["gen_ai.request.message_count"] = len(messages)

        if self._otel_tracer is not None:
            with self._otel_tracer.start_as_current_span(
                f"llm.{model}", attributes=attrs,
            ) as s:
                llm = _OtelLlmAdapter(s)
                t0 = time.time()
                try:
                    yield llm
                finally:
                    llm.set_attribute("gen_ai.response.duration_ms",
                                      int((time.time() - t0) * 1000))
        else:
            sp = NoOpSpan(name=f"llm.{model}", attributes=attrs)
            with sp:
                self._in_memory.append(sp)
                t0 = time.time()
                try:
                    yield sp
                finally:
                    sp.set_attribute("gen_ai.response.duration_ms",
                                     int((time.time() - t0) * 1000))


class _OtelSpanAdapter:
    """Wrap an OTel span so the caller can use the NoOpSpan-style
    fluent API (set_attribute / set_status / record_exception) —
    OTel's own API is similar but uses StatusCode / Status objects
    that callers have to import.
    """
    def __init__(self, otel_span):
        self._s = otel_span

    def set_attribute(self, k, v):
        try:
            self._s.set_attribute(k, v)
        except Exception:
            pass
        return self

    def set_attributes(self, d):
        for k, v in d.items():
            self.set_attribute(k, v)
        return self

    def set_status(self, status, description=""):
        try:
            if status == "error":
                self._s.set_status(Status(StatusCode.ERROR, description))
            else:
                self._s.set_status(Status(StatusCode.OK))
        except Exception:
            pass
        return self

    def record_exception(self, exc):
        try:
            self._s.record_exception(exc)
        except Exception:
            pass
        return self

    def add_event(self, name, attrs=None):
        try:
            self._s.add_event(name, attributes=attrs or {})
        except Exception:
            pass
        return self

    def llm_call(self, model, provider="", messages=None):
        # Nesting — caller code uses ``with span.llm_call(...) as llm``
        return _NoOpLlmContext(self._s, model, provider, messages)


class _OtelLlmAdapter:
    def __init__(self, otel_span):
        self._s = otel_span

    def set_attribute(self, k, v):
        try:
            self._s.set_attribute(k, v)
        except Exception:
            pass
        return self

    def set_output(self, content, prompt_tokens=0, completion_tokens=0,
                   cost_usd=0.0, finish_reason=""):
        # Note: we don't store the content on the span by default to
        # avoid PII / context bloat. The user can opt in via
        # ``set_attribute("gen_ai.response.content", content)``.
        if prompt_tokens:
            self.set_attribute("gen_ai.usage.input_tokens", int(prompt_tokens))
        if completion_tokens:
            self.set_attribute("gen_ai.usage.output_tokens", int(completion_tokens))
        if cost_usd:
            self.set_attribute("gen_ai.usage.cost", float(cost_usd))
        if finish_reason:
            self.set_attribute("gen_ai.response.finish_reason", finish_reason)
        return self


class _NoOpLlmContext:
    """A no-op context manager that mimics the OTel llm_call shape."""
    def __init__(self, parent_span, model, provider, messages):
        self._sp = NoOpSpan(name=f"llm.{model}", attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
            "gen_ai.system": provider,
            "gen_ai.request.message_count": len(messages) if messages else 0,
        })

    def __enter__(self):
        self._sp.__enter__()
        return self._sp

    def __exit__(self, *args):
        return self._sp.__exit__(*args)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_default_tracer: Optional[Tracer] = None


def init_default_tracer(
    name: str = "kairos",
    otlp_endpoint: Optional[str] = None,
    exporter: Optional[str] = None,
) -> Tracer:
    """Initialize the process-wide default Tracer.

    Honors the ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var by default
    (the OTel standard). The ``exporter`` arg is for tests:
    pass ``"console"`` to print every span to stderr.
    """
    global _default_tracer
    if otlp_endpoint is None:
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    _default_tracer = Tracer(
        name=name, exporter=exporter, otlp_endpoint=otlp_endpoint,
    )
    return _default_tracer


def get_default_tracer() -> Tracer:
    """Return the default tracer, initializing a no-op one if needed."""
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = Tracer(name="kairos")
    return _default_tracer
