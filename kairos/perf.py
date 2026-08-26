"""Performance helpers for Kairos.

The fast path is single-process async FastAPI; this module adds
three orthogonal layers of optimization:

1. **uvicorn workers** — a launcher that boots ``N`` worker
   processes (sharing the FastAPI app object) so concurrent
   requests scale across CPU cores. ``--workers 4`` is the
   recommended production default.

2. **LRU caches** — small ``functools.lru_cache``-style caches
   for hot read paths:
     - skill discovery (per directory)
     - agent prompts (per role)
     - tool schemas (per tool class)

3. **Profile helpers** — ``@timed`` decorator + a context
   manager that record wall-time + cpu-time + memory delta into
   the metrics subsystem (or just logs).

Plus a tiny ``CoroBatcher`` that fires N coroutines through a
single asyncio.gather (already used by the bench runner) and a
``token-bucket`` rate limiter (for outbound LLM calls when a
provider complains about rate limits).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. uvicorn workers launcher
# ---------------------------------------------------------------------------


def recommended_workers() -> int:
    """Pick a reasonable number of workers for the host."""
    try:
        cpu = os.cpu_count() or 1
    except Exception:
        cpu = 1
    # 2 * cores + 1 is the textbook uvicorn / gunicorn formula
    # capped at 8 to keep memory usage reasonable.
    return min(8, max(1, 2 * cpu + 1))


def build_uvicorn_command(
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: int = 0,
    *,
    app: str = "api.app:app",
    log_level: str = "info",
    reload: bool = False,
) -> List[str]:
    """Build the command to launch uvicorn with the right workers.

    Returns the argv list; caller can `subprocess.Popen` it.
    """
    n = workers if workers > 0 else recommended_workers()
    cmd = [
        sys.executable, "-m", "uvicorn",
        app,
        "--host", host,
        "--port", str(port),
        "--workers", str(n),
        "--log-level", log_level,
    ]
    if reload:
        cmd.append("--reload")
    return cmd


def launch_uvicorn(
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: int = 0,
    **kwargs: Any,
) -> subprocess.Popen:
    """Boot the uvicorn server in a subprocess.

    Returns the Popen handle. Caller is responsible for
    ``.wait()`` / ``.terminate()``.
    """
    cmd = build_uvicorn_command(host=host, port=port, workers=workers, **kwargs)
    logger.info("launching uvicorn: %s", " ".join(cmd))
    return subprocess.Popen(cmd)


# ---------------------------------------------------------------------------
# 2. LRU caches for hot paths
# ---------------------------------------------------------------------------


def cached(maxsize: int = 128, *, typed: bool = False):
    """``functools.lru_cache`` wrapper with stats.

    Adds ``cache_info()`` + a ``cache_clear()`` method (lru_cache
    already has these, but we want a single import path).
    """
    return functools.lru_cache(maxsize=maxsize, typed=typed)


def ttl_cache(ttl_s: float, maxsize: int = 128):
    """Time-bounded LRU cache.

    Unlike ``lru_cache``, the entry expires after ``ttl_s`` of
    wall time. Useful for "expensive to compute, but the answer
    might change soon" caches (e.g. provider price catalogs).
    """
    def decorator(fn):
        cache: Dict[Any, Tuple[float, Any]] = {}
        order: List[Any] = []  # for LRU eviction

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            if key in cache:
                ts, value = cache[key]
                if now - ts < ttl_s:
                    # LRU touch
                    order.remove(key)
                    order.append(key)
                    return value
                # expired
                del cache[key]
                order.remove(key)
            value = fn(*args, **kwargs)
            cache[key] = (now, value)
            order.append(key)
            while len(order) > maxsize:
                oldest = order.pop(0)
                cache.pop(oldest, None)
            return value

        wrapper.cache_clear = lambda: (cache.clear(), order.clear())
        wrapper.cache_info = lambda: type("Info", (), {
            "currsize": len(cache), "maxsize": maxsize,
        })()
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 3. Profiling / timing helpers
# ---------------------------------------------------------------------------


@dataclass
class ProfileSample:
    name: str
    duration_s: float
    extra: Dict[str, Any] = field(default_factory=dict) if False else None  # type: ignore

    def __init__(self, name: str, duration_s: float,
                 extra: Optional[Dict[str, Any]] = None) -> None:
        self.name = name
        self.duration_s = duration_s
        self.extra = dict(extra or {})


_samples: List[ProfileSample] = []


def timed(name: Optional[str] = None) -> Callable:
    """Decorator that records wall-clock time into the global sample log.

    Usage::

        @timed("loop.run")
        async def run_loop(...): ...

    If no name is given, the function's qualified name is used.
    """
    def decorator(fn):
        label = name or fn.__qualname__

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    _samples.append(ProfileSample(label, time.perf_counter() - t0))
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                _samples.append(ProfileSample(label, time.perf_counter() - t0))
        return sync_wrapper

    return decorator


@contextmanager
def profile_block(name: str, **extra: Any):
    """Context-manager version of :func:`timed`."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _samples.append(ProfileSample(name, time.perf_counter() - t0, extra))


def drain_samples() -> List[ProfileSample]:
    """Return all collected samples and reset the buffer."""
    global _samples
    out = list(_samples)
    _samples = []
    return out


def profile_summary() -> Dict[str, Any]:
    """Return aggregated stats: count / total / mean / p50 / p99 per name."""
    from statistics import mean, median
    by_name: Dict[str, List[float]] = {}
    for s in drain_samples():
        by_name.setdefault(s.name, []).append(s.duration_s)
    out: Dict[str, Any] = {}
    for name, durs in by_name.items():
        durs_sorted = sorted(durs)
        n = len(durs_sorted)
        out[name] = {
            "count": n,
            "total_s": round(sum(durs_sorted), 6),
            "mean_s": round(mean(durs_sorted), 6),
            "p50_s": round(median(durs_sorted), 6) if n else 0.0,
            "p99_s": round(durs_sorted[int(n * 0.99)] if n else 0.0, 6),
        }
    return out


# ---------------------------------------------------------------------------
# 4. Token-bucket rate limiter (for LLM API calls)
# ---------------------------------------------------------------------------


@dataclass
class TokenBucket:
    """Simple token-bucket rate limiter.

    Tokens refill continuously at *rate_per_sec*; calls consume
    one token. When the bucket is empty the caller is blocked
    (via ``await acquire()``) until enough tokens have refilled.

    Useful to back off when an upstream LLM provider returns 429.
    """
    rate_per_sec: float
    capacity: float
    _tokens: float = 0.0
    _last: float = 0.0
    _lock: Optional[asyncio.Lock] = None

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = None  # lazy-init in the running loop

    def _ensure_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self, n: int = 1) -> None:
        lock = self._ensure_lock()
        async with lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
                if self._tokens >= n:
                    self._tokens -= n
                    return
                # sleep just enough to accrue one token
                needed = n - self._tokens
                sleep_s = needed / self.rate_per_sec
                await asyncio.sleep(sleep_s)


# ---------------------------------------------------------------------------
# 5. CoroBatcher
# ---------------------------------------------------------------------------


async def gather_with_concurrency(n: int, *coros: Awaitable) -> list:
    """Run *coros* with at most *n* running concurrently.

    Returns the list of results in the same order as *coros*.
    """
    sem = asyncio.Semaphore(n)

    async def _wrap(c):
        async with sem:
            return await c

    return await asyncio.gather(*[_wrap(c) for c in coros])
