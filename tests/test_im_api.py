"""HTTP-level tests for the per-account IM endpoints.

These drive the real FastAPI app, so they cover the wiring as well as the
handlers: a route that is not mounted, or one whose signature check can be
skipped, fails here. The orchestrator is replaced with a fake (through the
same `get_orchestrator` dependency the handlers use), which keeps the tests
offline and lets them assert which project a conversation landed in.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.deps import get_orchestrator
from api.routes import im as im_routes
from kairos.core.orchestrator import Project
from kairos.im_accounts import (SIGNATURE_HEADER, TIMESTAMP_HEADER,
                                IMAccountStore, sign_request)


# --------------------------------------------------------------------- fakes

class _FakeCoder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(self, text: str, **kwargs: Any) -> str:
        self.calls.append(text)
        return f"reply: {text}"


class _FakeDB:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def save_message(self, message: Any) -> None:
        self.saved.append(message)


class _FakeOrchestrator:
    """A real Project with a fake agent behind it.

    The project object is the **real** class on purpose. A stand-in that
    invents its own attribute names hides a real AttributeError: this
    route shipped reading ``.project_id`` while a Project stores ``.id``,
    and only the live build noticed, because the fake had invented the
    matching name. Only the LLM-facing half is faked here.
    """

    def __init__(self, workspace_base: Path) -> None:
        self.workspace_base = workspace_base
        self.projects: Dict[str, Any] = {}
        self._db = _FakeDB()
        self.created: list[str] = []

    def create_project(self, name: str, description: str,
                       work_dir: str = "") -> Any:
        pid = f"proj{len(self.projects) + 1}"
        workspace = self.workspace_base / pid
        workspace.mkdir(parents=True, exist_ok=True)
        project = Project(pid, name, description, workspace, work_dir,
                          db=None)
        project.coder = _FakeCoder()
        self.projects[pid] = project
        self.created.append(name)
        return project

    def get_project(self, project_id: str) -> Any:
        return self.projects.get(project_id)


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def env(tmp_path):
    store = IMAccountStore(tmp_path / "im.db")
    asyncio.run(store.init())
    orch = _FakeOrchestrator(tmp_path / "workspaces")
    im_routes.set_store(store)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    # No context manager: that would run the real lifespan (MCP servers,
    # browser manager, the user's own data dir). The store is injected.
    client = TestClient(app)
    yield client, store, orch
    app.dependency_overrides.clear()
    im_routes.set_store(None)


@pytest.fixture(autouse=True)
def _offline_general_lane(monkeypatch):
    """Keep these wiring tests offline now that inbound consults the router.

    A signal-free inbound message (e.g. "hello") resolves to the general lane
    (round 37), and that lane would otherwise resolve the configured provider
    and make a real network call. Pinning "no model" makes the general lane
    fall back to the Coder -- i.e. exactly the pre-change behaviour -- so the
    tests below assert the wiring without leaving the machine. The routing
    itself is covered explicitly by the lane tests further down.
    """
    monkeypatch.setattr("kairos.skeleton.service.default_generator", lambda: None)


def signed(secret: str, body: bytes, ts: str | None = None) -> Dict[str, str]:
    stamp = ts or str(int(time.time()))
    return {TIMESTAMP_HEADER: stamp,
            SIGNATURE_HEADER: sign_request(secret, stamp, body)}


def make_account(store: IMAccountStore, account_id: str = "wx-a",
                 secret: str = "s3cret", enabled: bool = True) -> None:
    asyncio.run(store.upsert_account(account_id, name=account_id.upper(),
                                     secret=secret, enabled=enabled))


def post_inbound(client, account_id: str, secret: str, payload: dict,
                 ts: str | None = None):
    import json as _json
    body = _json.dumps(payload).encode("utf-8")
    return client.post(f"/api/im/{account_id}/inbound", content=body,
                       headers=signed(secret, body, ts))


# ------------------------------------------------------------------- accounts

def test_status_reports_the_store(env):
    client, store, _ = env
    make_account(store)
    r = client.get("/api/im/status")
    assert r.status_code == 200
    assert r.json()["accounts"] == 1
    assert r.json()["conversations"] == 0


def test_account_secret_is_never_echoed_back(env):
    client, _, _ = env
    r = client.post("/api/im/accounts", json={
        "account_id": "wx-a", "name": "A", "secret": "top-secret-value"})
    assert r.status_code == 200
    assert "top-secret-value" not in r.text
    listed = client.get("/api/im/accounts")
    assert "top-secret-value" not in listed.text
    assert listed.json()["accounts"][0]["account_id"] == "wx-a"


def test_a_bad_account_id_is_rejected(env):
    client, _, _ = env
    r = client.post("/api/im/accounts", json={"account_id": "../etc",
                                              "secret": "x"})
    assert r.status_code == 400


def test_deleting_an_account_removes_its_rows(env):
    client, store, _ = env
    make_account(store)
    post_inbound(client, "wx-a", "s3cret",
                 {"chat_id": "chat-1", "text": "hi"})
    assert client.delete("/api/im/accounts/wx-a").status_code == 200
    assert client.get("/api/im/bindings").json()["bindings"] == []
    assert client.delete("/api/im/accounts/wx-a").status_code == 404


# ------------------------------------------------------------------ signatures

def test_inbound_without_a_signature_is_refused(env):
    client, store, _ = env
    make_account(store)
    r = client.post("/api/im/wx-a/inbound",
                    json={"chat_id": "chat-1", "text": "hi"})
    assert r.status_code == 401
    assert r.json()["detail"] == "bad signature"


def test_inbound_with_a_wrong_secret_is_refused(env):
    client, store, _ = env
    make_account(store)
    r = post_inbound(client, "wx-a", "not-the-secret",
                     {"chat_id": "chat-1", "text": "hi"})
    assert r.status_code == 401


def test_a_stale_signature_cannot_be_replayed(env):
    """The timestamp is signed, so an old capture is not a free pass."""
    client, store, _ = env
    make_account(store)
    old = str(int(time.time()) - 600)
    r = post_inbound(client, "wx-a", "s3cret",
                     {"chat_id": "chat-1", "text": "hi"}, ts=old)
    assert r.status_code == 401


def test_unknown_account_is_404(env):
    client, _, _ = env
    r = post_inbound(client, "nobody", "s3cret",
                     {"chat_id": "chat-1", "text": "hi"})
    assert r.status_code == 404


def test_an_account_without_a_secret_fails_closed(env):
    """A half-configured account must not be an open door."""
    client, store, _ = env
    asyncio.run(store.upsert_account("wx-a", name="A", secret=""))
    r = client.post("/api/im/wx-a/inbound",
                    json={"chat_id": "chat-1", "text": "hi"})
    assert r.status_code == 403
    assert "no secret" in r.json()["detail"]


def test_a_disabled_account_is_refused(env):
    client, store, _ = env
    make_account(store, enabled=False)
    r = post_inbound(client, "wx-a", "s3cret",
                     {"chat_id": "chat-1", "text": "hi"})
    assert r.status_code == 403


def test_outbound_requires_a_signature(env):
    client, store, _ = env
    make_account(store)
    assert client.get("/api/im/wx-a/outbound").status_code == 401
    assert client.post("/api/im/wx-a/outbound/ack",
                       json={"ids": [1]}).status_code == 401


# --------------------------------------------------------------------- inbound

def test_inbound_creates_the_workspace_and_queues_the_reply(env):
    client, store, orch = env
    make_account(store)
    r = post_inbound(client, "wx-a", "s3cret",
                     {"chat_id": "chat-1", "text": "hello",
                      "chat_name": "小明"})
    assert r.status_code == 200
    body = r.json()
    assert body["created_project"] is True
    assert body["project_id"] in orch.projects
    assert orch.projects[body["project_id"]].coder.calls == ["hello"]
    # The reply is queued for the connector, not returned to be sent.
    queued = asyncio.run(store.pending("wx-a"))
    assert [m.text for m in queued] == ["reply: hello"]
    assert queued[0].chat_id == "chat-1"
    # The binding exists, and the project name mentions the account.
    assert asyncio.run(store.lookup("wx-a", "chat-1")) == body["project_id"]
    assert "wx-a" in orch.created[0].lower() or "A" in orch.created[0]


def test_a_second_message_reuses_the_conversation_workspace(env):
    client, store, orch = env
    make_account(store)
    first = post_inbound(client, "wx-a", "s3cret",
                         {"chat_id": "chat-1", "text": "one"}).json()
    second = post_inbound(client, "wx-a", "s3cret",
                          {"chat_id": "chat-1", "text": "two"}).json()
    assert second["created_project"] is False
    assert second["project_id"] == first["project_id"]
    assert len(orch.projects) == 1


def test_each_conversation_gets_its_own_workspace(env):
    """The isolation claim, at the HTTP layer.

    Two conversations on one account, and the *same* conversation id on a
    second account, must be three separate workspaces.
    """
    client, store, orch = env
    make_account(store, "wx-a", "s3cret")
    make_account(store, "wx-b", "other-secret")
    a1 = post_inbound(client, "wx-a", "s3cret",
                      {"chat_id": "chat-1", "text": "hi"}).json()
    a2 = post_inbound(client, "wx-a", "s3cret",
                      {"chat_id": "chat-2", "text": "hi"}).json()
    b1 = post_inbound(client, "wx-b", "other-secret",
                      {"chat_id": "chat-1", "text": "hi"}).json()
    ids = {a1["project_id"], a2["project_id"], b1["project_id"]}
    assert len(ids) == 3
    assert len(orch.projects) == 3
    # And the same chat id on two accounts resolves to different projects.
    assert asyncio.run(store.lookup("wx-a", "chat-1")) != \
        asyncio.run(store.lookup("wx-b", "chat-1"))


def test_inbound_refuses_empty_text_and_missing_chat(env):
    client, store, _ = env
    make_account(store)
    assert post_inbound(client, "wx-a", "s3cret",
                        {"chat_id": "chat-1", "text": "   "}).status_code == 400
    assert post_inbound(client, "wx-a", "s3cret",
                        {"text": "hi"}).status_code == 400
    assert post_inbound(client, "wx-a", "s3cret",
                        {"chat_id": "chat-1"}).status_code == 400


def test_the_message_is_saved_as_chat_history(env):
    """So the same thread is visible when the project is opened in the UI."""
    client, store, orch = env
    make_account(store)
    post_inbound(client, "wx-a", "s3cret",
                 {"chat_id": "chat-1", "text": "logged?"})
    assert len(orch._db.saved) == 1
    assert orch._db.saved[0].content == "logged?"


def test_an_agent_failure_does_not_queue_a_reply(env):
    client, store, orch = env
    make_account(store)

    async def boom(*args, **kwargs):
        raise RuntimeError("provider is down")

    class _Broken(_FakeOrchestrator):
        def create_project(self, name, description, work_dir=""):
            project = super().create_project(name, description, work_dir)
            project.coder.chat = boom
            return project

    orch.__class__ = _Broken  # type: ignore[misc]
    r = post_inbound(client, "wx-a", "s3cret",
                     {"chat_id": "chat-1", "text": "hi"})
    assert r.status_code == 502
    assert asyncio.run(store.pending("wx-a")) == []


# --------------------------------------------------------------------- routing
# Round 37: inbound goes through the same router the web /chat route uses. A
# coding-intent / long task keeps the Coder; a signal-free conversational
# message is answered on the general lane. These three tests pin the split and
# the no-regression guarantee (a plain message still yields a reply).


def test_a_coding_message_still_reaches_the_coder(env):
    client, store, orch = env
    make_account(store)
    text = "修复 src/auth 里的登录 bug"
    r = post_inbound(client, "wx-a", "s3cret", {"chat_id": "chat-1", "text": text})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    # The Coder lane, byte-for-byte as before the routing change.
    assert orch.projects[pid].coder.calls == [text]
    queued = asyncio.run(store.pending("wx-a"))
    assert [m.text for m in queued] == [f"reply: {text}"]


def test_a_plain_message_is_answered_on_the_general_lane(env, monkeypatch):
    """闲聊/问答走通用车道，且不复用 Coder。"""
    client, store, orch = env
    make_account(store)
    seen = []

    async def fake_general(*, kind, root, message, **kwargs):
        seen.append((kind, message))
        return "通用车道回复"

    monkeypatch.setattr("kairos.skeleton.service.run_chat_reply", fake_general)
    text = "你好呀，今天过得怎么样"
    r = post_inbound(client, "wx-a", "s3cret", {"chat_id": "chat-1", "text": text})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    assert seen == [("repo", text)]
    assert orch.projects[pid].coder.calls == []    # the Coder was NOT used
    queued = asyncio.run(store.pending("wx-a"))
    assert [m.text for m in queued] == ["通用车道回复"]


def test_a_plain_message_still_gets_a_reply_without_a_general_lane_model(env):
    """不回归：通用车道没有模型时回退 Coder，纯文本消息仍能拿到回复。

    The autouse fixture pins default_generator -> None, so the general lane
    yields nothing and the route must fall back -- never a silent no-reply.
    """
    client, store, orch = env
    make_account(store)
    r = post_inbound(client, "wx-a", "s3cret",
                     {"chat_id": "chat-1", "text": "今天天气不错呀"})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    assert orch.projects[pid].coder.calls == ["今天天气不错呀"]
    queued = asyncio.run(store.pending("wx-a"))
    assert [m.text for m in queued] == ["reply: 今天天气不错呀"]


# -------------------------------------------------------------------- outbound

def test_outbound_is_scoped_to_the_authenticated_account(env):
    """A connector must never see another account's mail."""
    client, store, _ = env
    make_account(store, "wx-a", "s3cret")
    make_account(store, "wx-b", "other-secret")
    post_inbound(client, "wx-a", "s3cret",
                 {"chat_id": "same-chat", "text": "for A"})
    post_inbound(client, "wx-b", "other-secret",
                 {"chat_id": "same-chat", "text": "for B"})

    body_a = b""
    r_a = client.get("/api/im/wx-a/outbound",
                     headers=signed("s3cret", body_a))
    r_b = client.get("/api/im/wx-b/outbound",
                     headers=signed("other-secret", body_a))
    assert [m["text"] for m in r_a.json()["messages"]] == ["reply: for A"]
    assert [m["text"] for m in r_b.json()["messages"]] == ["reply: for B"]
    # B's signature cannot read A's queue.
    crossed = client.get("/api/im/wx-a/outbound",
                         headers=signed("other-secret", body_a))
    assert crossed.status_code == 401


def test_ack_marks_delivered_and_is_scoped(env):
    client, store, _ = env
    make_account(store, "wx-a", "s3cret")
    make_account(store, "wx-b", "other-secret")
    post_inbound(client, "wx-a", "s3cret",
                 {"chat_id": "chat-1", "text": "mine"})
    victim = asyncio.run(store.pending("wx-a"))[0]

    # Account B cannot acknowledge account A's reply.
    import json as _json
    ack_body = _json.dumps({"ids": [victim.id]}).encode("utf-8")
    r = client.post("/api/im/wx-b/outbound/ack", content=ack_body,
                    headers=signed("other-secret", ack_body))
    assert r.json()["acked"] == 0
    assert asyncio.run(store.pending_count("wx-a")) == 1

    # Account A can, and then its queue is empty.
    r = client.post("/api/im/wx-a/outbound/ack", content=ack_body,
                    headers=signed("s3cret", ack_body))
    assert r.json()["acked"] == 1
    assert asyncio.run(store.pending_count("wx-a")) == 0


def test_account_counts_are_reported_for_the_panel(env):
    """What the Bots panel shows: queued replies and conversation count."""
    client, store, orch = env
    make_account(store)
    post_inbound(client, "wx-a", "s3cret",
                 {"chat_id": "chat-1", "text": "one"})
    post_inbound(client, "wx-a", "s3cret",
                 {"chat_id": "chat-2", "text": "two"})
    row = client.get("/api/im/accounts").json()["accounts"][0]
    assert row["account_id"] == "wx-a"
    assert row["enabled"] is True
    assert row["conversations"] == 2
    assert row["pending"] == 2  # one queued reply per conversation


# -------------------------------------------------------------------- bindings

def test_bindings_are_listed_per_account_and_can_be_removed(env):
    client, store, _ = env
    make_account(store, "wx-a", "s3cret")
    make_account(store, "wx-b", "other-secret")
    post_inbound(client, "wx-a", "s3cret",
                 {"chat_id": "chat-1", "text": "hi"})
    post_inbound(client, "wx-b", "other-secret",
                 {"chat_id": "chat-1", "text": "hi"})

    all_bindings = client.get("/api/im/bindings").json()["bindings"]
    assert len(all_bindings) == 2
    only_a = client.get("/api/im/bindings?account_id=wx-a").json()["bindings"]
    assert len(only_a) == 1

    r = client.delete("/api/im/bindings/wx-a/chat-1")
    assert r.status_code == 200
    assert len(client.get("/api/im/bindings").json()["bindings"]) == 1
    assert client.delete("/api/im/bindings/wx-a/chat-1").status_code == 404


def test_router_is_mounted_on_the_app(env):
    """Mounting is checked through the schema, not `app.routes`.

    `app.routes` is not a flat list of paths (it holds ~40 entries, some
    with no `.path` at all), so asking it for a route reports a false
    negative for a route that is in fact mounted.
    """
    paths = set(app.openapi()["paths"])
    for expected in ("/api/im/status", "/api/im/accounts",
                     "/api/im/bindings",
                     "/api/im/{account_id}/inbound",
                     "/api/im/{account_id}/outbound",
                     "/api/im/{account_id}/outbound/ack"):
        assert expected in paths, expected

# -------------------------------------------------------------------- pairing
#
# The click-to-scan path. Two views of one handshake: what the UI may see (a
# status, never a secret) and what the connector may do (everything, with the
# pairing secret it was handed when it claimed the code).

PNG_ISH = b"\x89PNG\r\n\x1a\n" + b"pixels" * 16


def test_pairing_handshake_ends_with_a_working_account(env):
    client, store, _orch = env

    created = client.post("/api/im/pairings").json()
    pairing_id = created["pairing_id"]
    assert created["ttl_seconds"] > 0

    view = client.get(f"/api/im/pairings/{pairing_id}").json()
    assert view["status"] == "waiting"
    assert view["has_qr"] is False
    assert "secret" not in view          # the UI never gets one

    claim = client.get("/api/im/pairings/pending")
    assert claim.status_code == 200
    claimed = claim.json()
    assert claimed["pairing_id"] == pairing_id
    assert claimed["secret"]
    headers = {"X-Pairing-Secret": claimed["secret"]}

    # The code is taken: a second connector gets nothing.
    assert client.get("/api/im/pairings/pending").status_code == 204

    # The QR may only be uploaded with the claimed secret.
    assert client.post(f"/api/im/pairings/{pairing_id}/qr",
                       content=PNG_ISH).status_code == 403
    assert client.post(f"/api/im/pairings/{pairing_id}/qr", content=PNG_ISH,
                       headers=headers).status_code == 200

    view = client.get(f"/api/im/pairings/{pairing_id}").json()
    assert view["status"] == "qr" and view["has_qr"] is True
    served = client.get(f"/api/im/pairings/{pairing_id}/qr.png")
    assert served.status_code == 200
    assert served.content == PNG_ISH
    assert served.headers["content-type"] == "image/png"

    assert client.post(f"/api/im/pairings/{pairing_id}/scanned",
                       headers=headers).status_code == 200
    assert client.get(f"/api/im/pairings/{pairing_id}").json()["status"] == "scanned"

    done = client.post(f"/api/im/pairings/{pairing_id}/confirm",
                       json={"account_id": "wx-scan", "display_name": "我的微信"},
                       headers=headers)
    assert done.status_code == 200
    account_secret = done.json()["secret"]
    assert account_secret

    accounts = {a["account_id"]: a
                for a in client.get("/api/im/accounts").json()["accounts"]}
    assert "wx-scan" in accounts
    assert accounts["wx-scan"]["name"] == "我的微信"
    assert "secret" not in accounts["wx-scan"]

    # The handed-over secret is the real one: it signs inbound traffic.
    assert post_inbound(client, "wx-scan", account_secret,
                        {"chat_id": "chat-1", "text": "hi"}).status_code == 200

    # One shot: the handshake cannot be replayed into a second account.
    replay = client.post(f"/api/im/pairings/{pairing_id}/confirm",
                         json={"account_id": "wx-other"}, headers=headers)
    assert replay.status_code in (400, 403)
    assert "wx-other" not in {
        a["account_id"] for a in client.get("/api/im/accounts").json()["accounts"]}


def test_pairing_requires_the_secret_and_expires(env):
    client, store, _orch = env
    pairing_id = client.post("/api/im/pairings").json()["pairing_id"]
    assert client.get("/api/im/pairings/pending").json()["pairing_id"] == pairing_id

    wrong = {"X-Pairing-Secret": "not-it"}
    assert client.post(f"/api/im/pairings/{pairing_id}/qr", content=PNG_ISH,
                       headers=wrong).status_code == 403
    assert client.post(f"/api/im/pairings/{pairing_id}/scanned",
                       headers=wrong).status_code == 403
    assert client.post(f"/api/im/pairings/{pairing_id}/confirm",
                       json={"account_id": "wx-no"}, headers=wrong).status_code == 403
    assert client.get("/api/im/pairings/nosuchid").status_code == 404

    # An expired code is not claimable, so it can never become an account.
    import asyncio
    asyncio.run(store.create_pairing(ttl_seconds=-1))
    assert client.get("/api/im/pairings/pending").status_code == 204
