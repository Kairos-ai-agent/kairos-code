"""The lifespan must *record* subsystem failures, not swallow them silently.

Background: the startup blocks each wrap a subsystem in their own ``try`` so a
failure degrades only that feature — that design stays. What used to be missing
is any *structured* trace of a failure: a swallowed ``NameError`` here once took
the approval channel, the long-running registry and the daemon supervisor down
as a single log line, and nobody noticed the gate had stopped asking for
permission (``kairos/sentinel.py``: no channel ⇒ an ASK verdict falls through to
allow). Log lines are not something a test can assert on.

So ``api/app.py`` keeps ``STARTUP_FAILURES`` (read via
``api.app.get_startup_failures()`` and mirrored on ``app.state.startup_failures``)
and verifies every ``_STARTUP_SUBSYSTEMS`` entry is live after assembly.
These tests assert the observable outcome — the record and the probes — by
running the real lifespan through ``TestClient``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.app as app_module
from api.app import (
    STARTUP_FAILURES,
    _STARTUP_SUBSYSTEMS,
    app,
    get_startup_failures,
)


@pytest.fixture(autouse=True)
def _reset_startup_state():
    """Start each test from 'nothing wired' so a stale singleton can't pass it."""
    from kairos import approvals
    import kairos.autonomous_worker as aw
    import kairos.daemon as dm
    import kairos.long_running as lr

    approvals.set_channel(None)
    lr._REGISTRY = None
    dm._SUPERVISOR = None
    aw._WORKER = None
    for attr in ("_browser_manager", "_feishu_bot", "_feishu_store",
                 "_feishu_forwarder", "_wecom_forwarder", "_im_store",
                 "_weixin_store", "_weixin_channel", "_weixin_approval_bridge"):
        setattr(app_module, attr, None)
    STARTUP_FAILURES.clear()
    yield
    STARTUP_FAILURES.clear()


def _browser_starts_ok(monkeypatch):
    """Make the browser manager a no-op so a 'clean' run needs no real browser."""
    from kairos.browser import BrowserManager

    async def _ok(self, *a, **kw):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(BrowserManager, "start", _ok, raising=True)
    monkeypatch.setattr(BrowserManager, "stop", _ok, raising=True)


def test_a_clean_startup_records_no_failures(monkeypatch):
    """Normal lifespan ⇒ the failure record is empty (and mirrored on app.state)."""
    _browser_starts_ok(monkeypatch)
    with TestClient(app):
        failures = get_startup_failures()
        assert failures == [], (
            "a clean startup recorded failures — some subsystem did not come up: "
            f"{failures!r}"
        )
        assert app.state.startup_failures == []


def test_every_registered_subsystem_is_alive_after_startup(monkeypatch):
    """Iterate the registry, not three hard-coded names, and require each live.

    This is the guard that makes a newly added-but-unwired subsystem fail a
    test: add an entry to ``_STARTUP_SUBSYSTEMS`` and its probe must be truthy
    once the lifespan has run.
    """
    _browser_starts_ok(monkeypatch)
    with TestClient(app):
        dead = [name for name, probe in _STARTUP_SUBSYSTEMS if not _probe(probe)]
    assert dead == [], (
        "registered subsystem(s) reported 'not alive' after startup: "
        f"{dead!r} — the lifespan never actually brought them up"
    )


def _probe(probe) -> bool:
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001
        return False


def test_a_failed_subsystem_is_recorded_and_named(monkeypatch):
    """Make the browser start fail; it must be recorded by name and not silent.

    It also must not take the other subsystems down with it (that regression —
    the browser try containing the approval/registry/daemon blocks — is why
    these are separate ``try``s now).
    """
    from kairos.browser import BrowserManager
    from kairos import approvals
    from kairos.daemon import get_supervisor

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise RuntimeError("no browser in this environment (test)")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)

    with TestClient(app):
        failures = get_startup_failures()
        named = {f["subsystem"] for f in failures}
        assert "browser_manager" in named, (
            "the browser startup failure was swallowed — it is not in the "
            f"structured record: {failures!r}"
        )
        # Exactly one failure: the browser failure degraded alone.
        assert named == {"browser_manager"}, (
            "the browser failure leaked into other subsystems: "
            f"{failures!r}"
        )
        assert app_module._browser_manager is None
        # The subsystems that must not depend on the browser are still up.
        assert approvals.get_channel() is not None
        assert get_supervisor() is not None
        # And the record entry carries a usable error string, not an empty one.
        entry = next(f for f in failures if f["subsystem"] == "browser_manager")
        assert "RuntimeError" in entry["error"]
        assert entry["phase"] == "startup"


def test_the_failure_record_is_also_mirrored_on_app_state(monkeypatch):
    """The read entry is reachable both ways the task asked for."""
    from kairos.browser import BrowserManager

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise RuntimeError("boom (test)")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)
    with TestClient(app):
        assert app.state.startup_failures == get_startup_failures()
        assert any(f["subsystem"] == "browser_manager"
                   for f in app.state.startup_failures)
