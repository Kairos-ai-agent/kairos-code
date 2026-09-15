"""R38.9 — the update endpoints are wired, and honest about what they can do.

These run through the repo's own app (via pytest, which puts the working copy
first on ``sys.path``), so a missing ``include_router`` fails here.
"""

import pytest
from fastapi.testclient import TestClient

from kairos import updater


@pytest.fixture
def client():
    from api.app import app
    return TestClient(app)


def test_status_reports_what_this_install_is(client):
    r = client.get("/api/update/status")
    assert r.status_code == 200
    body = r.json()
    assert body["version"]
    assert body["installKind"] in ("frozen", "wheel", "source")
    assert isinstance(body["canSelfUpdate"], bool)
    assert body["reason"]                     # always say *why* (or "ok")
    assert "enabled" in body


def test_check_is_read_only_and_needs_no_network(client, monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {"hasUpdate": False, "current": "0.1.4", "latest": "0.1.4",
                "reason": "ok", "asset": None}

    monkeypatch.setattr(updater, "check_for_update", fake)
    r = client.get("/api/update/check")
    assert r.status_code == 200
    assert r.json()["hasUpdate"] is False
    assert calls == [{"force": False}]

    r = client.get("/api/update/check?force=true")
    assert r.status_code == 200
    assert calls[-1] == {"force": True}


def test_apply_reports_why_it_did_not_run(client, monkeypatch):
    monkeypatch.setattr(updater, "apply_update", lambda **kw: {
        "ok": False, "stage": None, "helper": None,
        "restartRequired": False, "reason": "not-a-packaged-build", "error": None,
    })
    r = client.post("/api/update/apply")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "not-a-packaged-build"
    assert body["restartRequired"] is False
