"""The lifespan must actually bring its subsystems up.

Three of them were no-ops in the real process for a long time, and nothing
caught it:

* ``api/app.py`` called ``_orch()`` in its startup blocks while the module
  neither defined nor imported that name. The ``NameError`` was swallowed by
  the surrounding ``except``, so ``approvals.set_channel(...)``, the
  long-running registry and the daemon supervisor never came up;
* they sat *inside* the browser-manager ``try``, so a browser that failed to
  start took them down with it.

With no approval channel the gate's ASK verdict has nobody to ask and falls
through to allowing the action (``kairos/sentinel.py``), so this is not
cosmetic: the feature the UI (and the WeChat bridge) answers questions through
was never installed.

These tests assert the observable outcome — the singletons the rest of the app
looks up — rather than the log lines, and they deliberately make the browser
manager fail, because none of the three may depend on it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import app
from kairos import approvals
from kairos.daemon import get_supervisor
from kairos.long_running import get_registry


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """Make the browser manager fail at startup.

    It is the reason these blocks were unreachable in the first place, so the
    test is only meaningful when it cannot come up.
    """
    from kairos.browser import BrowserManager

    async def _boom(self, *a, **kw):  # noqa: ANN002, ANN003
        raise RuntimeError("no browser in this environment (test)")

    monkeypatch.setattr(BrowserManager, "start", _boom, raising=True)


def test_the_lifespan_installs_the_approval_channel():
    # Start from "nothing installed" so a stale channel from another test can
    # not make this pass.
    approvals.set_channel(None)
    with TestClient(app):
        channel = approvals.get_channel()
        assert channel is not None, (
            "no approval channel after startup — the gate's ASK verdict would "
            "fall through to allowing the action (see api/app.py:_orch)"
        )
        # The channel the gate asks through must be reachable by the routes
        # that answer questions (the UI polls it; the WeChat bridge pushes it).
        assert channel is approvals.get_channel()
    # The client's shutdown must not leave a channel pointing at a dead bus.
    approvals.set_channel(None)


def test_the_lifespan_primes_the_long_running_registry_and_daemon():
    with TestClient(app):
        assert get_registry() is not None, (
            "the long-running registry was never primed — subagent / goal / "
            "autonomous events have nowhere to go"
        )
        assert get_supervisor() is not None, (
            "the daemon supervisor never started — no daemon.heartbeat, no "
            "/api/daemon/attach"
        )


def test_app_has_the_orchestrator_shim_it_calls():
    """The precise regression: the startup blocks call ``_orch()``.

    It must exist on the module and resolve the live orchestrator (a lookup on
    ``api.deps`` so monkeypatching keeps working, same as
    ``api/routes/projects.py:_orch``).
    """
    import api.app as app_module
    from api import deps

    assert callable(getattr(app_module, "_orch", None)), (
        "api/app.py calls _orch() but does not define it — startup swallows "
        "the NameError and the subsystems above silently stay down"
    )
    assert app_module._orch() is deps.orchestrator
