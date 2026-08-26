"""Tests for the kairos.main entry point + worker resolution.

We don't actually launch uvicorn (the existing
``test_perf.py::test_launch_uvicorn_spawns_and_terminates`` does
that). Here we just verify the worker-count + loop-selection
logic in :func:`kairos.main._resolve_workers` /
:func:`kairos.main._resolve_loop`.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest


def test_resolve_workers_zero_uses_recommended(monkeypatch):
    """``workers=0`` (default) picks min(8, 2*cpu+1)."""
    from kairos.main import _resolve_workers
    from kairos.config.settings import Settings

    # Build a Settings directly with workers=0 + debug=False
    s = Settings(workers=0, debug=False)
    monkeypatch.setattr("kairos.main.settings", s)
    n = _resolve_workers()
    assert 1 <= n <= 8


def test_resolve_workers_explicit_honored(monkeypatch):
    """``workers=N`` is passed through unchanged."""
    from kairos.main import _resolve_workers
    from kairos.config.settings import Settings

    s = Settings()
    s.workers = 4
    s.debug = False
    monkeypatch.setattr("kairos.config.settings.settings", s)
    assert _resolve_workers() == 4


def test_resolve_workers_debug_forces_one(monkeypatch):
    """debug=True forces workers=1 (uvicorn's --reload requires it)."""
    from kairos.main import _resolve_workers
    from kairos.config.settings import Settings

    s = Settings()
    s.workers = 8
    s.debug = True
    monkeypatch.setattr("kairos.config.settings.settings", s)
    assert _resolve_workers() == 1


def test_resolve_loop_auto_returns_uvloop_on_posix_with_uvloop(monkeypatch):
    from kairos.main import _resolve_loop
    from kairos.config.settings import Settings

    s = Settings()
    s.loop = "auto"
    monkeypatch.setattr("kairos.config.settings.settings", s)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.name", "posix")

    fake_uvloop = type(sys.modules["sys"])("uvloop")  # any module object
    with patch.dict(sys.modules, {"uvloop": fake_uvloop}):
        assert _resolve_loop() == "uvloop"


def test_resolve_loop_auto_returns_asyncio_on_posix_without_uvloop(monkeypatch):
    from kairos.main import _resolve_loop
    from kairos.config.settings import Settings

    s = Settings()
    s.loop = "auto"
    monkeypatch.setattr("kairos.config.settings.settings", s)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.name", "posix")
    # Pretend uvloop is not installed
    monkeypatch.delitem(sys.modules, "uvloop", raising=False)

    real_import = (__builtins__["__import__"]
                   if isinstance(__builtins__, dict)
                   else __builtins__.__import__)

    def fake_import(name, *args, **kwargs):
        if name == "uvloop":
            raise ImportError("uvloop")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        assert _resolve_loop() == "asyncio"


def test_resolve_loop_auto_returns_asyncio_on_windows(monkeypatch):
    from kairos.main import _resolve_loop
    from kairos.config.settings import Settings

    s = Settings(loop="auto")
    monkeypatch.setattr("kairos.main.settings", s)
    monkeypatch.setattr("sys.platform", "win32")
    # Even if uvloop is importable, Windows should fall back to asyncio
    fake = type(sys.modules["sys"])("uvloop")
    with patch.dict(sys.modules, {"uvloop": fake}):
        assert _resolve_loop() == "asyncio"


def test_resolve_loop_explicit_overrides_auto(monkeypatch):
    from kairos.main import _resolve_loop
    from kairos.config.settings import Settings

    s = Settings()
    s.loop = "asyncio"
    monkeypatch.setattr("kairos.config.settings.settings", s)
    assert _resolve_loop() == "asyncio"


def test_main_invokes_uvicorn_with_resolved_workers(monkeypatch):
    """The startup pipeline calls uvicorn.run with the resolved
    worker count and loop."""
    import kairos.main as main_mod
    from kairos.config.settings import Settings

    s = Settings()
    s.workers = 2
    s.debug = False
    s.loop = "asyncio"
    s.host = "127.0.0.1"
    s.port = 18888
    monkeypatch.setattr("kairos.config.settings.settings", s)
    with patch("uvicorn.run") as mock_run:
        try:
            main_mod.main()
        except SystemExit:
            pass
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["workers"] == 2
        assert kwargs["loop"] == "asyncio"


def test_settings_default_workers_is_zero():
    """The default workers=0 means 'auto-pick based on CPU'."""
    from kairos.config.settings import Settings
    s = Settings()
    assert s.workers == 0


def test_settings_default_loop_is_auto():
    from kairos.config.settings import Settings
    s = Settings()
    assert s.loop == "auto"
