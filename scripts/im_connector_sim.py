"""A minimal connector: sends one message, collects the replies.

This is the reference implementation of the connector contract, and the
thing to run when you want to know whether the Kairos side works without
involving WeChat at all. A real connector does exactly this, with a
logged-in client in the middle:

    message arrives  -> POST /api/im/{account}/inbound   (signed)
    every few sec    -> GET  /api/im/{account}/outbound  (signed)
    after sending    -> POST /api/im/{account}/outbound/ack

Signing is HMAC-SHA256 over ``timestamp + "\\n" + body`` with the
account's own secret, so this file doubles as the specification of that
scheme.

    python scripts/im_connector_sim.py account add wx-a --secret s3cret
    python scripts/im_connector_sim.py chat --account wx-a --base http://127.0.0.1:7777
    python scripts/im_connector_sim.py chat --account wx-a --text "你好" --once
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the very same signing helper the endpoint verifies with, so the
# tool cannot drift from the protocol it is documenting.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kairos.im_accounts import (SIGNATURE_HEADER, TIMESTAMP_HEADER,  # noqa: E402
                                sign_request)


def _request(base: str, method: str, path: str, secret: str,
             payload: Optional[Dict[str, Any]] = None,
             timeout: float = 120.0) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    stamp = str(int(time.time()))
    req = urllib.request.Request(
        url=base.rstrip("/") + path, data=body or None, method=method,
        headers={
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: stamp,
            SIGNATURE_HEADER: sign_request(secret, stamp, body),
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"HTTP {exc.code} on {method} {path}: {detail}")


def add_account(base: str, account_id: str, name: str, secret: str,
                enabled: bool = True) -> Any:
    """Management call: no signature (the local API is unauthenticated)."""
    payload = json.dumps({"account_id": account_id, "name": name,
                          "secret": secret, "enabled": enabled}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/im/accounts", data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def collect(base: str, account_id: str, secret: str) -> List[Dict[str, Any]]:
    """Grab and acknowledge whatever this account has waiting."""
    got = _request(base, "GET", f"/api/im/{account_id}/outbound", secret)
    messages = got.get("messages") or []
    if messages:
        _request(base, "POST", f"/api/im/{account_id}/outbound/ack", secret,
                 {"ids": [m["id"] for m in messages]})
    return messages


def send(base: str, account_id: str, secret: str, chat_id: str,
         text: str, chat_name: str = "") -> Dict[str, Any]:
    return _request(base, "POST", f"/api/im/{account_id}/inbound", secret, {
        "chat_id": chat_id, "text": text, "chat_name": chat_name,
    })


def cmd_account_add(args: argparse.Namespace) -> int:
    out = add_account(args.base, args.account_id, args.name or args.account_id,
                      args.secret, enabled=not args.disabled)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """Send one message, then collect replies until nothing is pending."""
    sent = send(args.base, args.account, args.secret, args.chat_id,
                args.text, chat_name=args.chat_name)
    print(f"sent -> project {sent.get('project_id')} "
          f"(created={sent.get('created_project')})")
    if args.once:
        return 0
    print("waiting for the reply (Ctrl-C to stop)...")
    deadline = time.time() + args.wait
    seen = 0
    while time.time() < deadline:
        for message in collect(args.base, args.account, args.secret):
            seen += 1
            print(f"\n[{args.account} -> {message['chat_id']}]\n"
                  f"{message['text']}\n")
        if args.stop_when_empty and seen:
            break
        time.sleep(args.interval)
    if not seen:
        print("no reply arrived in the window")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Stand-in for a real connector's receive loop: print and ack."""
    print(f"watching {args.account} at {args.base} (Ctrl-C to stop)...")
    while True:
        for message in collect(args.base, args.account, args.secret):
            print(f"\n[{args.account} -> {message['chat_id']}]\n"
                  f"{message['text']}\n")
        time.sleep(args.interval)


# --------------------------------------------------------------------- pairing
#
# The click-to-scan path from the connector's side. Nothing here is typed into
# the app by a human: the connector claims the waiting code, puts the chat
# client's login QR where the UI can show it, and on a successful scan reports
# the account it ended up logged in as. The account secret it gets back is
# written to a file and never printed -- a secret in scrollback is a leaked
# secret, and this one signs every inbound message.

def _raw(base: str, method: str, path: str, body: Optional[bytes] = None,
         headers: Optional[Dict[str, str]] = None) -> Any:
    """A pairing call: no account signature, only the pairing's own secret."""
    req = urllib.request.Request(base.rstrip("/") + path, data=body,
                                 method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.status == 204 or not raw:
                return {}
            if resp.headers.get("Content-Type", "").startswith("image/"):
                return raw
            return json.loads(raw.decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return {}
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"HTTP {exc.code} on {method} {path}: {detail}")


def _png(width: int, height: int, rows: List[bytes]) -> bytes:
    import struct
    import zlib
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def qr_placeholder_png(seed: str, modules: int = 25, scale: int = 8) -> bytes:
    """A code-shaped image, for a simulator that has no chat client.

    The real one is the client's own login QR: the client draws it, the
    connector re-uploads it, the user scans it. With no client there is nothing
    to re-upload, so this draws something with the right proportions -- and the
    command says out loud that it is a placeholder, because a phone held up to
    it would do nothing.
    """
    import hashlib
    bits = "".join(f"{b:08b}"
                   for b in hashlib.sha256(seed.encode()).digest() * 32)
    size = modules * scale
    rows: List[bytes] = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            mx, my = x // scale, y // scale
            dark = False
            if 1 <= mx < modules - 1 and 1 <= my < modules - 1:
                finder = ((mx < 8 and my < 8) or (mx >= modules - 9 and my < 8)
                          or (mx < 8 and my >= modules - 9))
                if finder:
                    fx, fy = mx % 8, my % 8
                    dark = (fx in (0, 7) or fy in (0, 7)
                            or (2 <= fx <= 5 and 2 <= fy <= 5))
                else:
                    dark = bits[(my * modules + mx) % len(bits)] == "1"
            row += b"\x00\x00\x00" if dark else b"\xff\xff\xff"
        rows.append(bytes(row))
    return _png(size, size, rows)


def cmd_pair(args: argparse.Namespace) -> int:
    base = args.base
    print(f"[pair] waiting for a pairing on {base} "
          "(click 'Connect WeChat' in the app) ...")
    pairing: Dict[str, Any] = {}
    waited = 0.0
    while not pairing and waited < args.wait:
        pairing = _raw(base, "GET", "/api/im/pairings/pending")
        if not pairing:
            time.sleep(1.5)
            waited += 1.5
    if not pairing:
        print("[pair] nothing waiting -- start the handshake in the app first.")
        return 1

    pairing_id = str(pairing["pairing_id"])
    secret = str(pairing["secret"])
    print(f"[pair] claimed {pairing_id}")

    png = qr_placeholder_png(pairing_id)
    _raw(base, "POST", f"/api/im/pairings/{pairing_id}/qr", png,
         {"X-Pairing-Secret": secret, "Content-Type": "image/png"})
    print("[pair] uploaded a PLACEHOLDER code -- the app is showing it now. "
          "A real one comes from the chat client, not from here.")

    if not args.auto:
        try:
            input("[pair] press Enter once the scan is done ... ")
        except EOFError:
            pass

    _raw(base, "POST", f"/api/im/pairings/{pairing_id}/scanned", b"",
         {"X-Pairing-Secret": secret})
    payload = json.dumps({"account_id": args.account_id,
                          "display_name": args.name or args.account_id}).encode()
    done = _raw(base, "POST", f"/api/im/pairings/{pairing_id}/confirm", payload,
                {"X-Pairing-Secret": secret,
                 "Content-Type": "application/json"})

    account_secret = str(done.get("secret") or "")
    out_path = Path(args.secret_out)
    out_path.write_text(json.dumps(
        {"base": base, "account_id": done.get("account_id"),
         "secret": account_secret}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[pair] bound as {done.get('account_id')}")
    print(f"[pair] account secret written to {out_path} "
          "(deliberately not printed)")
    print("[pair] next:  python scripts/im_connector_sim.py chat "
          f"--account {done.get('account_id')} --secret <from that file>")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:9527",
                        help="Kairos base URL")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("account", help="account management")
    add_sub = p_add.add_subparsers(dest="action", required=True)
    p_add_a = add_sub.add_parser("add", help="create or update an account")
    p_add_a.add_argument("account_id")
    p_add_a.add_argument("--secret", required=True)
    p_add_a.add_argument("--name", default="")
    p_add_a.add_argument("--disabled", action="store_true")
    p_add_a.set_defaults(func=cmd_account_add)

    p_chat = sub.add_parser("chat", help="send a message and collect replies")
    p_chat.add_argument("--account", required=True)
    p_chat.add_argument("--secret", required=True)
    p_chat.add_argument("--chat-id", default="sim-chat-1")
    p_chat.add_argument("--chat-name", default="模拟会话")
    p_chat.add_argument("--text", default="你好，帮我看下今天的日程")
    p_chat.add_argument("--once", action="store_true",
                        help="send only, do not wait for the reply")
    p_chat.add_argument("--wait", type=float, default=300.0)
    p_chat.add_argument("--interval", type=float, default=3.0)
    p_chat.add_argument("--stop-when-empty", action="store_true")
    p_chat.set_defaults(func=cmd_chat)

    p_watch = sub.add_parser("watch", help="print and ack replies forever")
    p_watch.add_argument("--account", required=True)
    p_watch.add_argument("--secret", required=True)
    p_watch.add_argument("--interval", type=float, default=3.0)
    p_watch.set_defaults(func=cmd_watch)

    p_pair = sub.add_parser("pair", help="scan-to-bind: claim a pairing, show its code")
    p_pair.add_argument("--account-id", required=True,
                        help="the account id this login should become")
    p_pair.add_argument("--name", default="", help="display name for the account")
    p_pair.add_argument("--wait", type=float, default=120.0,
                        help="seconds to wait for a pairing to appear")
    p_pair.add_argument("--auto", action="store_true",
                        help="do not wait for the Enter prompt")
    p_pair.add_argument("--secret-out", default=".im-connector-secret.json",
                        help="where to write the account secret")
    p_pair.set_defaults(func=cmd_pair)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
