"""Tests for the kairos.perf helpers."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from kairos.perf import (
    TokenBucket,
    build_uvicorn_command,
    cached,
    drain_samples,
    gather_with_concurrency,
    launch_uvicorn,
    profile_block,
    profile_summary,
    recommended_workers,
    timed,
    ttl_cache,
)


# ---------------------------------------------------------------------------
# 1. uvicorn workers
# ---------------------------------------------------------------------------


def test_recommended_workers_at_least_one():
    n = recommended_workers()
    assert n >= 1
    assert n <= 8


def test_build_uvicorn_command_basic():
    cmd = build_uvicorn_command(host="127.0.0.1", port=9000, workers=2)
    assert "--host" in cmd and "127.0.0.1" in cmd
    assert "--port" in cmd and "9000" in cmd
    assert "--workers" in cmd and "2" in cmd
    assert "api.app:app" in cmd


def test_build_uvicorn_command_zero_workers_uses_recommended():
    cmd = build_uvicorn_command(workers=0)
    # workers=0 should fall back to recommended_workers()
    assert str(recommended_workers()) in cmd


def test_launch_uvicorn_spawns_and_terminates():
    """Smoke test: launch and immediately kill. The server should
    exit cleanly (we don't actually wait for the listening socket)."""
    proc = launch_uvicorn(host="127.0.0.1", port=18765, workers=1,
                           log_level="warning")
    try:
        # Give it a moment to bind.
        time.sleep(0.5)
        # The process should still be running.
        assert proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ---------------------------------------------------------------------------
# 2. LRU + TTL caches
# ---------------------------------------------------------------------------


def test_cached_basic():
    calls = []

    @cached(maxsize=8)
    def add(a, b):
        calls.append((a, b))
        return a + b

    assert add(1, 2) == 3
    assert add(1, 2) == 3
    assert add(1, 2) == 3
    # only one underlying call thanks to the cache
    assert calls == [(1, 2)]


def test_cached_distinguishes_args():
    @cached(maxsize=8)
    def fn(x):
        return x * 2

    assert fn(1) == 2
    assert fn(2) == 4
    assert fn(1) == 2  # cached


def test_ttl_cache_expires():
    calls = []

    @ttl_cache(ttl_s=0.1, maxsize=8)
    def fn(x):
        calls.append(x)
        return x

    assert fn(1) == 1
    assert fn(1) == 1
    assert calls == [1]  # cached
    time.sleep(0.15)
    assert fn(1) == 1
    assert calls == [1, 1]  # expired, recomputed


def test_ttl_cache_evicts_lru():
    @ttl_cache(ttl_s=10, maxsize=2)
    def fn(x):
        return x

    fn(1); fn(2); fn(3)  # 1 should be evicted
    assert fn.cache_info().currsize == 2


def test_ttl_cache_clear():
    @ttl_cache(ttl_s=10, maxsize=8)
    def fn(x):
        return x

    fn(1); fn(2)
    fn.cache_clear()
    assert fn.cache_info().currsize == 0


# ---------------------------------------------------------------------------
# 3. Profiling
# ---------------------------------------------------------------------------


def test_timed_sync_records_sample():
    drain_samples()  # clean slate

    @timed("test.sync_fn")
    def f():
        time.sleep(0.01)
        return 42

    assert f() == 42
    samples = drain_samples()
    assert len(samples) == 1
    assert samples[0].name == "test.sync_fn"
    assert samples[0].duration_s >= 0.01


@pytest.mark.asyncio
async def test_timed_async_records_sample():
    drain_samples()

    @timed("test.async_fn")
    async def f():
        await asyncio.sleep(0.01)
        return "ok"

    assert await f() == "ok"
    samples = drain_samples()
    assert samples[0].name == "test.async_fn"
    assert samples[0].duration_s >= 0.01


def test_timed_uses_qualname_when_no_name():
    drain_samples()

    @timed()
    def my_unique_function_xyz():
        return None

    my_unique_function_xyz()
    samples = drain_samples()
    assert "my_unique_function_xyz" in samples[0].name


def test_profile_block_records_with_extras():
    drain_samples()
    with profile_block("test.block", user="alice"):
        pass
    samples = drain_samples()
    assert samples[0].name == "test.block"
    assert samples[0].extra == {"user": "alice"}


def test_profile_summary_aggregates():
    drain_samples()

    @timed("hot.path")
    def f():
        time.sleep(0.001)

    for _ in range(5):
        f()

    summary = profile_summary()
    assert "hot.path" in summary
    s = summary["hot.path"]
    assert s["count"] == 5
    assert s["mean_s"] > 0
    assert s["p50_s"] > 0
    assert s["p99_s"] > 0


# ---------------------------------------------------------------------------
# 4. Token bucket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_bucket_initial_capacity_allows_burst():
    bucket = TokenBucket(rate_per_sec=1, capacity=5)
    # 5 immediate acquires should succeed without sleep
    start = time.perf_counter()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5  # basically instant


@pytest.mark.asyncio
async def test_token_bucket_throttles_overflow():
    bucket = TokenBucket(rate_per_sec=100, capacity=2)  # 2 initial, 100/sec refill
    start = time.perf_counter()
    # 5 acquires with capacity 2 + rate 100/sec → ~30ms of throttling
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.02  # some throttling happened
    assert elapsed < 2.0    # but not crazy


@pytest.mark.asyncio
async def test_token_bucket_acquire_n():
    bucket = TokenBucket(rate_per_sec=10, capacity=10)
    await bucket.acquire(n=5)
    # 4 more tokens available → next acquire(5) should also work
    await bucket.acquire(n=4)
    # 1 token left, but we want 5 → should wait
    start = time.perf_counter()
    await bucket.acquire(n=5)
    elapsed = time.perf_counter() - start
    # 4 tokens missing at 10/sec = 0.4s
    assert elapsed >= 0.3
    assert elapsed < 2.0


# ---------------------------------------------------------------------------
# 5. gather_with_concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_with_concurrency_runs_in_parallel():
    async def task(i):
        await asyncio.sleep(0.05)
        return i * 2

    start = time.perf_counter()
    results = await gather_with_concurrency(10, *[task(i) for i in range(10)])
    elapsed = time.perf_counter() - start
    assert sorted(results) == [i * 2 for i in range(10)]
    # All 10 ran concurrently; total ≈ 0.05s, well under sequential 0.5s.
    assert elapsed < 0.3


@pytest.mark.asyncio
async def test_gather_with_concurrency_limits_concurrency():
    """With concurrency=2, tasks 3+ should wait for 1+2 to finish."""
    peak = 0
    current = 0
    lock = asyncio.Lock()

    async def task(i):
        nonlocal peak, current
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.05)
        async with lock:
            current -= 1
        return i

    await gather_with_concurrency(2, *[task(i) for i in range(6)])
    assert peak == 2


@pytest.mark.asyncio
async def test_gather_with_concurrency_preserves_order():
    async def task(i):
        # Vary sleep so completion order ≠ spawn order
        await asyncio.sleep(0.01 * (5 - i))
        return i

    results = await gather_with_concurrency(10, *[task(i) for i in range(5)])
    # gather preserves order regardless of completion order
    assert results == [0, 1, 2, 3, 4]
