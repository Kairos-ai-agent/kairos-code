"""Per-account IM endpoints: accounts, bindings, inbound, outbound.

This is the Kairos side of the "many accounts, one deployment" contract.
The connector that drives a logged-in WeChat client lives **outside** this
repository on purpose -- it carries login state and injects into one
specific desktop client build, and none of that belongs in an open-source
tree. What lives here is the protocol it speaks:

    POST /api/im/{account_id}/inbound        connector -> Kairos
    GET  /api/im/{account_id}/outbound       connector <- Kairos
    POST /api/im/{account_id}/outbound/ack   connector -> Kairos

    POST /api/im/pairings                    UI -> Kairos (start the handshake)
    GET  /api/im/pairings/pending            connector -> Kairos (claim it)
    POST /api/im/pairings/{id}/qr            connector -> Kairos (its login QR)
    POST /api/im/pairings/{id}/scanned       connector -> Kairos
    POST /api/im/pairings/{id}/confirm       connector -> Kairos (the account)

Design notes worth keeping in mind when editing this file:

* **Kairos never sends a message.** It queues one. The connector that
  owns the account's login collects it and posts it through that account,
  so a reply cannot leave through the wrong account even if the routing
  logic were wrong.
* **Every signed request is scoped to one account**, and the signature is
  keyed by that account's own secret. A connector config that leaks
  exposes one account's traffic, not every account's.
* **A project is the workspace boundary.** Each conversation gets its own
  project, which already means its own workspace directory, its own agent
  instance and its own checkpoints -- the isolation is existing code, and
  nothing here weakens it.
* These management endpoints (accounts, bindings) are unauthenticated,
  like the rest of the local API. The app is meant to be reached over
  loopback; exposing the port to a network without a reverse proxy in
  front is a decision to make deliberately, not by accident.

Route order matters: the literal paths are declared before the
``/{account_id}/...`` ones so that "accounts" and "bindings" are never
parsed as an account id.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, HTTPException, Query,
                     Request, Response)
from pydantic import BaseModel

from api.deps import get_orchestrator
from kairos.im_accounts import (SIGNATURE_HEADER, TIMESTAMP_HEADER,
                                 IMAccountError, IMAccountStore,
                                 validate_account_id, verify_request)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/im", tags=["im"])

# Module-level state, wired by api/app.py (same shape as the Feishu
# integration). The orchestrator is resolved per request through
# `Depends(get_orchestrator)` so tests that monkeypatch the singleton see
# it.
_store: Optional[IMAccountStore] = None


def set_store(store: IMAccountStore) -> None:
    global _store
    _store = store


def _get_store() -> IMAccountStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="im not initialized")
    return _store


async def _authenticate(account_id: str, request: Request):
    """Authenticate a connector request as exactly one account.

    Fails closed on every count: unknown account (404), disabled account
    (403), no secret configured (403 -- a half-configured account is not
    an open door), or a bad/stale signature (401).
    """
    store = _get_store()
    try:
        account_id = validate_account_id(account_id)
    except IMAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    account = await store.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404,
                            detail=f"unknown account: {account_id}")
    if not account.enabled:
        raise HTTPException(status_code=403,
                            detail=f"account disabled: {account_id}")
    secret = await store.get_secret(account_id) or ""
    if not secret:
        raise HTTPException(
            status_code=403,
            detail=f"account has no secret configured: {account_id}")
    raw = await request.body()
    ok = verify_request(
        secret,
        request.headers.get(TIMESTAMP_HEADER, ""),
        raw,
        request.headers.get(SIGNATURE_HEADER, ""),
    )
    if not ok:
        raise HTTPException(status_code=401, detail="bad signature")
    return account


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

class AccountBody(BaseModel):
    account_id: str
    name: str = ""
    secret: str = ""
    enabled: bool = True


@router.get("/accounts")
async def list_accounts():
    """All accounts, plus how much mail each one has waiting."""
    store = _get_store()
    accounts = await store.list_accounts()
    out: List[Dict[str, Any]] = []
    for a in accounts:
        out.append({
            "account_id": a.account_id,
            "name": a.name,
            "enabled": a.enabled,
            "created_at": a.created_at,
            "pending": await store.pending_count(a.account_id),
            "conversations": len(await store.list_bindings(a.account_id)),
        })
    return {"accounts": out}


@router.post("/accounts")
async def upsert_account(body: AccountBody):
    """Create or update an account.

    The secret is accepted here and never echoed back -- the response is
    the public view, which has no secret field at all.
    """
    store = _get_store()
    try:
        account = await store.upsert_account(
            body.account_id, name=body.name, secret=body.secret,
            enabled=body.enabled)
    except IMAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "account": {
        "account_id": account.account_id,
        "name": account.name,
        "enabled": account.enabled,
        "created_at": account.created_at,
    }}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    """Remove an account together with its bindings and its queue."""
    store = _get_store()
    try:
        account_id = validate_account_id(account_id)
    except IMAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if await store.get_account(account_id) is None:
        raise HTTPException(status_code=404,
                            detail=f"unknown account: {account_id}")
    await store.delete_account(account_id)
    return {"ok": True, "deleted": account_id}


# ---------------------------------------------------------------------------
# Bindings (conversation -> project)
# ---------------------------------------------------------------------------

@router.get("/bindings")
async def list_bindings(account_id: Optional[str] = Query(default=None)):
    store = _get_store()
    try:
        return {"bindings": await store.list_bindings(account_id)}
    except IMAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/bindings/{account_id}/{chat_id}")
async def delete_binding(account_id: str, chat_id: str):
    """Forget a conversation's workspace binding.

    The project itself is left alone: it still holds the conversation's
    history, and deleting a project is a separate, visible action.
    """
    store = _get_store()
    try:
        account_id = validate_account_id(account_id)
    except IMAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if await store.lookup(account_id, chat_id) is None:
        raise HTTPException(status_code=404, detail="no such binding")
    await store.unbind(account_id, chat_id)
    return {"ok": True, "account_id": account_id, "chat_id": chat_id}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status")
async def status():
    """One-line health view for the UI and for manual testing."""
    store = _get_store()
    accounts = await store.list_accounts()
    total_pending = sum([await store.pending_count(a.account_id)
                         for a in accounts])
    return {
        "initialized": True,
        "accounts": len(accounts),
        "enabled": sum(1 for a in accounts if a.enabled),
        "conversations": len(await store.list_bindings()),
        "pending": total_pending,
    }


# ---------------------------------------------------------------------------
# Connector protocol (signed, per account)
# ---------------------------------------------------------------------------

# --------------------------------------------------------------------- pairing
#
# The user's path to a connected bot, and why it is shaped like this: nobody
# wants to invent an account id, copy a secret into a config file and paste
# four URLs. They click once, a QR code appears, they scan it with the app they
# already have, and the connector on their machine reports which account it is
# logged in as. Secrets still exist -- every inbound request is signed with one
# -- but they are made here and handed over inside that same handshake, so they
# are never typed, copied, or shown to anyone.

QR_MAX_BYTES = 512 * 1024


async def _pairing_or_404(pairing_id: str):
    pairing = await _get_store().get_pairing(pairing_id)
    if pairing is None:
        raise HTTPException(status_code=404, detail="unknown pairing")
    return pairing


async def _claimed_or_403(pairing_id: str, request: Request):
    """Verify the pairing's own secret -- it is the connector's credential."""
    pairing = await _pairing_or_404(pairing_id)
    if not hmac.compare_digest(pairing.secret,
                              request.headers.get("x-pairing-secret", "")):
        raise HTTPException(status_code=403, detail="pairing secret rejected")
    return pairing


@router.post("/pairings")
async def create_pairing():
    """Start a pairing. Called when the user clicks "connect" in the UI."""
    pairing = await _get_store().create_pairing()
    return {"pairing_id": pairing.pairing_id,
            "expires_at": pairing.expires_at,
            "ttl_seconds": IMAccountStore.PAIRING_TTL_SECONDS}


# Declared before /pairings/{pairing_id}; "pending" is not an id.
@router.get("/pairings/pending")
async def pending_pairing(request: Request):
    """The connector's first call: claim whatever pairing is waiting."""
    pairing = await _get_store().claim_pairing(
        request.headers.get("x-connector", ""))
    if pairing is None:
        return Response(status_code=204)
    return {"pairing_id": pairing.pairing_id, "secret": pairing.secret,
            "expires_at": pairing.expires_at}


@router.get("/pairings/{pairing_id}")
async def get_pairing(pairing_id: str):
    """Status for the UI: never a secret, never the QR bytes."""
    return (await _pairing_or_404(pairing_id)).to_dict()


@router.get("/pairings/{pairing_id}/qr.png")
async def get_pairing_qr(pairing_id: str):
    png = await _get_store().pairing_qr(pairing_id)
    if not png:
        raise HTTPException(status_code=404, detail="no qr yet")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.delete("/pairings/{pairing_id}")
async def cancel_pairing(pairing_id: str):
    await _get_store().delete_pairing(pairing_id)
    return {"ok": True}


@router.post("/pairings/{pairing_id}/qr")
async def upload_pairing_qr(pairing_id: str, request: Request):
    """The connector puts the chat client's login QR here for the UI to show."""
    await _claimed_or_403(pairing_id, request)
    body = await request.body()
    if not body or len(body) > QR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="unusable QR payload")
    ok = await _get_store().set_pairing_qr(
        pairing_id, request.headers.get("x-pairing-secret", ""), body)
    if not ok:
        raise HTTPException(status_code=403, detail="pairing secret rejected")
    return {"ok": True, "bytes": len(body)}


@router.post("/pairings/{pairing_id}/scanned")
async def mark_pairing_scanned(pairing_id: str, request: Request):
    await _claimed_or_403(pairing_id, request)
    ok = await _get_store().set_pairing_status(
        pairing_id, request.headers.get("x-pairing-secret", ""), "scanned")
    if not ok:
        raise HTTPException(status_code=403, detail="pairing secret rejected")
    return {"ok": True}


class ConfirmBody(BaseModel):
    account_id: str
    display_name: str = ""


@router.post("/pairings/{pairing_id}/confirm")
async def confirm_pairing(pairing_id: str, body: ConfirmBody, request: Request):
    """Last call: the account is created and its inbound secret handed over."""
    await _claimed_or_403(pairing_id, request)
    try:
        secret = await _get_store().confirm_pairing(
            pairing_id, request.headers.get("x-pairing-secret", ""),
            body.account_id, body.display_name)
    except IMAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if secret is None:
        raise HTTPException(status_code=403, detail="pairing not confirmable")
    return {"account_id": body.account_id, "secret": secret}


class InboundBody(BaseModel):
    chat_id: str
    text: str
    chat_name: str = ""
    sender: str = ""
    message_id: str = ""


class AckBody(BaseModel):
    ids: List[int] = []


def _project_label(account, chat_id: str, chat_name: str) -> str:
    """A name a human can recognise in the project list.

    Prefixed with the account so the same person on two accounts stays
    two obviously-different projects.
    """
    who = (account.name or account.account_id).strip()
    chat = (chat_name or chat_id).strip()
    label = f"{who} · {chat}"
    return label[:60]


@router.post("/{account_id}/inbound")
async def inbound(account_id: str, request: Request,
                  orchestrator=Depends(get_orchestrator)):
    """A message arrived in one conversation on one account.

    The conversation is routed to its own project; the first message in a
    conversation creates that project. The reply is **queued**, not
    returned for the connector to send -- one delivery path means a reply
    cannot be sent twice by a connector that also reads the outbox.
    """
    store = _get_store()
    account = await _authenticate(account_id, request)
    account_id = account.account_id
    raw = await request.body()
    try:
        body = InboundBody(**json.loads(raw or b"{}"))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400,
                            detail=f"bad request body: {exc}") from exc

    text = (body.text or "").strip()
    chat_id = (body.chat_id or "").strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Route to this conversation's own workspace.
    project_id = await store.lookup(account_id, chat_id)
    project = orchestrator.get_project(project_id) if project_id else None
    created = False
    if project is None:
        # A binding whose project is gone is not an error worth failing
        # on: the conversation gets a fresh workspace.
        project = orchestrator.create_project(
            name=_project_label(account, chat_id, body.chat_name),
            description=(f"IM conversation on account {account_id} "
                         f"(chat {chat_id})"),
        )
        # Project stores its identifier as `.id` (its to_dict exposes it
        # as "id" too) -- not `.project_id`.
        project_id = project.id
        await store.bind(account_id, chat_id, project_id)
        created = True
        logger.info("im: created project %s for %s/%s",
                    project_id, account_id, chat_id)

    if not project.coder:
        raise HTTPException(
            status_code=502,
            detail=f"conversation workspace {project_id} has no agent")

    # Keep the desktop chat history in sync with the conversation, so the
    # same thread is visible when the project is opened in the UI.
    try:
        from kairos.core.message_bus import Message as _BusMessage
        orchestrator._db.save_message(_BusMessage(
            sender="user", receiver=f"{project_id}.coder",
            topic="user.chat", content=text, msg_type="text",
            metadata={"project_id": project_id, "account_id": account_id,
                      "chat_id": chat_id, "source": "im"},
        ))
    except Exception:  # noqa: BLE001 - history is a nicety, not the job
        logger.exception("im: failed to persist inbound message")

    try:
        reply = await project.coder.chat(text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("im: chat failed for %s/%s", account_id, chat_id)
        raise HTTPException(
            status_code=502,
            detail=f"agent failed: {type(exc).__name__}") from exc

    queued_id = 0
    if (reply or "").strip():
        queued_id = await store.enqueue(account_id, chat_id, reply)

    return {
        "ok": True,
        "account_id": account_id,
        "chat_id": chat_id,
        "project_id": project_id,
        "created_project": created,
        "queued_id": queued_id,
    }


@router.get("/{account_id}/outbound")
async def outbound(account_id: str, request: Request,
                   limit: int = Query(default=50, ge=1, le=500)):
    """Undelivered replies for **this** account, oldest first.

    Collecting does not consume: the connector acknowledges with
    ``/outbound/ack`` once the messages are actually sent.
    """
    store = _get_store()
    account = await _authenticate(account_id, request)
    messages = await store.pending(account.account_id, limit=limit)
    return {
        "account_id": account.account_id,
        "messages": [{"id": m.id, "chat_id": m.chat_id, "text": m.text,
                      "created_at": m.created_at} for m in messages],
    }


@router.post("/{account_id}/outbound/ack")
async def outbound_ack(account_id: str, request: Request):
    """Mark replies delivered. Scoped to the authenticated account."""
    store = _get_store()
    account = await _authenticate(account_id, request)
    raw = await request.body()
    try:
        body = AckBody(**json.loads(raw or b"{}"))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400,
                            detail=f"bad request body: {exc}") from exc
    acked = await store.ack(account.account_id, body.ids)
    return {"ok": True, "acked": acked}
