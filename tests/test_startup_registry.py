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

It also keeps ``STARTUP_SKIPS``: an *optional* subsystem that a given build
legitimately does not ship (``browser_manager`` — playwright is the optional
``browser`` extra) is recorded as a skip, not a failure, so the failure list
stays a signal that something is actually wrong. The last two tests here are the
guard rails on that: optionality must never excuse a real fault, and a *required*
subsystem must never be excused by it.

These tests assert the observable outcome — the record and the probes — by
running the real lifespan through ``TestClient``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.app as app_module
from api.app import (
    STARTUP_FAILURES,
    STARTUP_SKIPS,
    _STARTUP_SUBSYSTEMS,
    app,
    get_startup_failures,
    get_startup_skips,
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
    STARTUP_SKIPS.clear()
    yield
    STARTUP_FAILURES.clear()
    STARTUP_SKIPS.clear()


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
        assert app.state.startup_skips == get_startup_skips()


def test_every_registered_subsystem_is_alive_after_startup(monkeypatch):
    """Iterate the registry, not three hard-coded names, and require each live.

    This is the guard that makes a newly added-but-unwired subsystem fail a
    test: add an entry to ``_STARTUP_SUBSYSTEMS`` and its probe must be truthy
    once the lifespan has run.
    """
    _browser_starts_ok(monkeypatch)
    with TestClient(app):
        dead = [name for name, probe, _optional in _STARTUP_SUBSYSTEMS
                if not _probe(probe)]
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

    ``_browser_is_optional`` is pinned to ``None`` here: this test is about a
    browser that *should* come up and did not (playwright present), so the
    failure must be recorded rather than excused as an optional-missing skip.
    """
    from kairos.browser import BrowserManager
    from kairos import approvals
    from kairos.daemon import get_supervisor

    monkeypatch.setattr(app_module, "_browser_is_optional", lambda: None)

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
        # A real fault is never filed as a skip.
        assert not any(s["subsystem"] == "browser_manager"
                       for s in get_startup_skips())


def test_the_failure_record_is_also_mirrored_on_app_state(monkeypatch):
    """The read entry is reachable both ways the task asked for."""
    from kairos.browser import BrowserManager

    monkeypatch.setattr(app_module, "_browser_is_optional", lambda: None)

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise RuntimeError("boom (test)")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)
    with TestClient(app):
        assert app.state.startup_failures == get_startup_failures()
        assert any(f["subsystem"] == "browser_manager"
                   for f in app.state.startup_failures)


# ---------------------------------------------------------------------------
# The optional subsystem: skipped, not failed — and that can never mask a fault.
# ---------------------------------------------------------------------------

def test_an_optional_subsystem_is_skipped_not_failed(monkeypatch):
    """playwright absent ⇒ the browser manager is a legitimate skip.

    The failure list must stay a list where every entry means something is
    wrong; a base/packaged build has no browser and that is not a fault.
    """
    from kairos.browser import BrowserManager

    # playwright absent (the extra is not installed) — the real reason string.
    monkeypatch.setattr(app_module, "_browser_is_optional",
                        lambda: "playwright is an optional extra (test)")

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise ModuleNotFoundError("No module named 'playwright'")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)

    with TestClient(app):
        failures = get_startup_failures()
        skips = get_startup_skips()

    assert not any(f["subsystem"] == "browser_manager" for f in failures), (
        "an optional subsystem was recorded as a failure: " f"{failures!r}")
    names = [s["subsystem"] for s in skips]
    assert names.count("browser_manager") == 1, (
        f"the skip was not recorded exactly once: {skips!r}")
    entry = next(s for s in skips if s["subsystem"] == "browser_manager")
    assert "playwright" in entry["reason"], entry


def test_optionality_cannot_mask_a_real_browser_failure(monkeypatch):
    """The nail: playwright present (optional_when ⇒ None) but no manager ⇒ failure.

    This is the case optionality must *not* swallow: the extra is installed, so
    a browser that still did not start is a genuine fault and belongs in
    ``STARTUP_FAILURES``, never in ``STARTUP_SKIPS``.
    """
    from kairos.browser import BrowserManager

    monkeypatch.setattr(app_module, "_browser_is_optional", lambda: None)

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise RuntimeError("no browser in this environment (test)")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)

    with TestClient(app):
        failures = get_startup_failures()
        skips = get_startup_skips()

    assert any(f["subsystem"] == "browser_manager" for f in failures), (
        f"a real browser fault was excused by optionality: {failures!r}")
    assert not any(s["subsystem"] == "browser_manager" for s in skips), (
        f"a real browser fault was misfiled as a skip: {skips!r}")


@pytest.mark.parametrize("name, attr", [
    ("feishu", "_feishu_store"),
    ("wecom", "_wecom_forwarder"),
    ("im_store", "_im_store"),
    ("weixin_ilink", "_weixin_store"),
])
def test_required_subsystems_are_never_excused_by_optionality(
        monkeypatch, name, attr):
    """The other 8 subsystems are required: not alive ⇒ a failure, never a skip.

    Probes are run directly against the 'nothing wired' state the fixture
    leaves, so each named subsystem is genuinely not alive.
    """
    monkeypatch.setattr(app_module, attr, None)
    app_module._verify_startup_subsystems()
    failures = {f["subsystem"] for f in get_startup_failures()}
    skips = {s["subsystem"] for s in get_startup_skips()}
    assert name in failures, f"{name} must be a failure, got {failures!r}"
    assert name not in skips, f"{name} was wrongly filed as a skip"


# ---------------------------------------------------------------------------
# Part 2: the record is reachable over HTTP.
# ---------------------------------------------------------------------------

def test_sentinel_status_exposes_the_startup_record(monkeypatch):
    """``GET /api/sentinel/status`` appends both records, as lists."""
    from kairos.browser import BrowserManager

    monkeypatch.setattr(app_module, "_browser_is_optional", lambda: None)

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise RuntimeError("boom (test)")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)

    with TestClient(app) as client:
        body = client.get("/api/sentinel/status").json()
        assert isinstance(body["startup_failures"], list)
        assert isinstance(body["startup_skips"], list)
        assert any(f["subsystem"] == "browser_manager"
                   for f in body["startup_failures"])
    # The keys the frontend already reads are untouched.
    assert "mode" in body and "policy" in body and "always_refused" in body


def test_sentinel_status_carries_the_skip_record(monkeypatch):
    """...and the skip record rides along (browser absent ⇒ it is a skip)."""
    from kairos.browser import BrowserManager

    monkeypatch.setattr(app_module, "_browser_is_optional",
                        lambda: "playwright is an optional extra (test)")

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise ModuleNotFoundError("No module named 'playwright'")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)

    with TestClient(app) as client:
        body = client.get("/api/sentinel/status").json()
    assert any(s["subsystem"] == "browser_manager"
               for s in body["startup_skips"])
    assert not any(f["subsystem"] == "browser_manager"
                   for f in body["startup_failures"])
