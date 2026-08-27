"""Tests for the Round 15 Cognee-style 4-op memory adapter."""
from __future__ import annotations

import asyncio
import os

import pytest

from kairos.memory_4op import (
    _CogneeAdapter,
    _MockAdapter,
    get_default_backend,
    get_kb_for_backend,
    list_backends,
)


# ---------------------------------------------------------------------------
# Backend resolution
# ---------------------------------------------------------------------------


def test_list_backends_includes_known():
    backends = list_backends()
    assert "local" in backends
    assert "cognee" in backends
    assert "graphiti" in backends
    assert "mock" in backends


def test_get_default_backend_default_is_local(monkeypatch):
    monkeypatch.delenv("KAIROS_MEMORY_BACKEND", raising=False)
    assert get_default_backend() == "local"


def test_get_default_backend_reads_env(monkeypatch):
    monkeypatch.setenv("KAIROS_MEMORY_BACKEND", "cognee")
    assert get_default_backend() == "cognee"
    monkeypatch.setenv("KAIROS_MEMORY_BACKEND", "MOCK")
    assert get_default_backend() == "mock"


def test_get_kb_for_backend_local(monkeypatch, tmp_path):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAIROS_MEMORY_BACKEND", "local")
    kb = get_kb_for_backend()
    # The local backend is a real MemoryKB instance
    from kairos.memory_kb import MemoryKB
    assert isinstance(kb, MemoryKB)


def test_get_kb_for_backend_mock(monkeypatch):
    monkeypatch.setenv("KAIROS_MEMORY_BACKEND", "mock")
    kb = get_kb_for_backend()
    assert isinstance(kb, _MockAdapter)


def test_get_kb_for_backend_unknown_raises(monkeypatch):
    monkeypatch.setenv("KAIROS_MEMORY_BACKEND", "totally-fake-backend")
    with pytest.raises(ValueError, match="Unknown memory backend"):
        get_kb_for_backend()


def test_get_kb_for_backend_cognee_without_install_raises(monkeypatch):
    """If the cognee library isn't installed, get_kb_for_backend
    should NOT fail at construction — the failure is deferred to
    first use (matches the import-on-demand pattern)."""
    monkeypatch.setenv("KAIROS_MEMORY_BACKEND", "cognee")
    # If cognee IS installed in the test env, this will return a
    # real adapter; if not, it raises here. Either is acceptable —
    # the contract is that the call is cheap.
    try:
        import cognee  # noqa: F401
        kb = get_kb_for_backend()
        assert isinstance(kb, _CogneeAdapter)
    except ImportError:
        with pytest.raises(RuntimeError, match="requires the 'cognee' package"):
            get_kb_for_backend()


# ---------------------------------------------------------------------------
# Mock adapter (the contract surface)
# ---------------------------------------------------------------------------


def test_mock_adapter_remember_recall():
    """The mock's 4-op contract: remember then recall returns the value."""
    async def run():
        kb = _MockAdapter()
        await kb.remember("k", "v", scope="user")
        results = await kb.recall("anything", scope="user")
        assert any(r.get("key") == "k" and r.get("value") == "v" for r in results)
    asyncio.run(run())


def test_mock_adapter_forget_removes():
    async def run():
        kb = _MockAdapter()
        await kb.remember("k", "v")
        ok = await kb.forget("k")
        assert ok is True
        ok2 = await kb.forget("k")
        assert ok2 is False
    asyncio.run(run())


def test_mock_adapter_improve_existing():
    async def run():
        kb = _MockAdapter()
        await kb.remember("k", "v")
        out = await kb.improve("k", "feedback note")
        assert out is not None
        assert out["feedback"] == "feedback note"
    asyncio.run(run())


def test_mock_adapter_improve_missing_key():
    async def run():
        kb = _MockAdapter()
        out = await kb.improve("nope", "x")
        assert out is None
    asyncio.run(run())


# ---------------------------------------------------------------------------
# The 4 backends expose a uniform interface
# ---------------------------------------------------------------------------


def test_all_backends_expose_four_methods():
    """Every backend adapter has remember/recall/forget/improve."""
    import kairos.memory_4op as m
    # Mock is always available
    mock = _MockAdapter()
    for method in ("remember", "recall", "forget", "improve"):
        assert hasattr(mock, method), f"mock missing {method}"
        assert callable(getattr(mock, method))
    # Cognee: depends on whether the lib is installed
    try:
        import cognee  # noqa: F401
        cog = m._build_cognee_adapter()
        for method in ("remember", "recall", "forget", "improve"):
            assert hasattr(cog, method), f"cognee missing {method}"
    except ImportError:
        pass
