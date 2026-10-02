"""Per-account IM plumbing: accounts, bindings, and the outbound queue.

``kairos.feishu`` models **one bot per deployment**: a single webhook URL
that belongs to one group, with a ``feishu_bindings`` table keyed by
``chat_id`` alone. That is the right shape for "one group, many
projects", and the wrong shape for "many accounts, one deployment" --
in two specific ways.

* ``chat_id`` as the primary key means two accounts that see the same
  chat identifier overwrite each other's binding. The same friend on two
  accounts, or two groups whose opaque ids collide, would silently end up
  sharing one workspace.
* the outbound path is fixed by the credential. A custom-bot webhook can
  only post into the group it was created in, so a reply cannot be
  addressed to the conversation it came from -- replies from unrelated
  conversations all land in one place.

Both are answered by making the account a first-class dimension:
bindings are keyed ``(account_id, chat_id)``, and **Kairos does not send
anything itself**. Replies are queued here and collected by the connector
that owns that account's login, so a message physically cannot leave
through the wrong account.

Isolation, concretely:

* ``pending()`` filters by ``account_id``: one account's queue is never
  visible to another;
* every binding points at its own project, and a project already owns its
  working directory, its agent instance and its checkpoints -- the
  workspace boundary is existing code, not new code;
* account secrets live in this store (the runtime data directory, never
  the repository), are never returned by ``list_accounts()``, and are
  never logged.

This module is protocol only. The connector that drives a logged-in
WeChat client deliberately lives outside the repository: it depends on
injecting into one specific desktop client build, it carries the login
state, and none of that belongs in an open-source tree.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# An account id names a binding namespace, so it shows up in URLs and in
# the connector's configuration. Keep it boring and filesystem-safe.
MAX_ACCOUNT_ID_LEN = 64


class IMAccountError(ValueError):
    """Raised for malformed account or binding input."""


def validate_account_id(account_id: str) -> str:
    """Return a cleaned account id, or raise.

    Deliberately strict: this string becomes part of a URL path and a
    database key, and a loose one (slashes, spaces, case variants) is how
    two connectors end up believing they are the same account.
    """
    cleaned = (account_id or "").strip()
    if not cleaned:
        raise IMAccountError("account_id must not be empty")
    if len(cleaned) > MAX_ACCOUNT_ID_LEN:
        raise IMAccountError(
            f"account_id longer than {MAX_ACCOUNT_ID_LEN} characters")
    if not all(c.isalnum() or c in "-_." for c in cleaned):
        raise IMAccountError(
            "account_id may only contain letters, digits, '-', '_' and '.'")
    return cleaned


@dataclass
class Pairing:
    """A one-shot handshake with a connector.

    The user clicks "connect" in the UI and gets a QR code; the connector on
    their own machine claims the pairing, puts the chat client's login QR there,
    and on a successful scan reports which account it ended up logged in as.
    The account's inbound secret is generated here and handed over in that last
    call, so neither side has to type a secret -- and nobody has to invent an
    account id before the chat client has even been scanned.

    The pairing secret guards the connector-facing calls: the UI never sees it
    (it is not in ``to_dict``), and the one-shot delivery of the account secret
    happens exactly once.
    """

    pairing_id: str
    secret: str
    status: str                     # waiting | claimed | qr | scanned | bound | expired
    created_at: float
    expires_at: float
    connector: str = ""
    account_id: str = ""
    display_name: str = ""
    has_qr: bool = False

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict:
        """What the UI may see: no secret, and no QR bytes."""
        return {
            "pairing_id": self.pairing_id,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
            "display_name": self.display_name,
            "has_qr": self.has_qr,
            "expired": self.expired,
        }


@dataclass
class IMAccount:
    """Public view of an account -- note the absence of a secret field."""

    account_id: str
    name: str = ""
    enabled: bool = True
    created_at: float = 0.0


# ------------------------------------------------------------------ signatures
#
# A connector authenticates as **one account**, not as "the app". The
# signature is keyed by that account's own secret, so a connector config
# that leaks exposes one account's traffic instead of every account's.

SIGNATURE_HEADER = "X-Kairos-Signature"
TIMESTAMP_HEADER = "X-Kairos-Timestamp"
MAX_CLOCK_SKEW_S = 300


def sign_request(secret: str, timestamp: str, body: bytes) -> str:
    """The signature a connector presents for one request."""
    mac = hmac.new(secret.encode("utf-8"),
                   timestamp.encode("utf-8") + b"\n" + (body or b""),
                   hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_request(secret: str, timestamp: str, body: bytes,
                   signature: str, *, now: Optional[float] = None,
                   max_skew: int = MAX_CLOCK_SKEW_S) -> bool:
    """Constant-time check of a connector request, inside a clock window.

    The timestamp is part of the signed material, so a captured request
    cannot simply be replayed later -- the window is what makes that true
    in practice and not only in principle. An account with no secret set
    fails closed, so a half-configured account is not an open door.
    """
    if not secret or not signature or not timestamp:
        return False
    try:
        age = abs((now if now is not None else time.time()) - float(timestamp))
    except (TypeError, ValueError):
        return False
    if age > max_skew:
        return False
    return hmac.compare_digest(sign_request(secret, timestamp, body),
                               signature)


@dataclass
class OutboundMessage:
    """One queued reply, addressed to exactly one conversation."""

    id: int
    account_id: str
    chat_id: str
    text: str
    created_at: float


class IMAccountStore:
    """SQLite store for accounts, chat bindings and the outbound queue.

    One file, three tables, no cross-account query that is not written
    with an explicit ``account_id`` predicate.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS im_accounts (
                    account_id TEXT PRIMARY KEY,
                    name       TEXT NOT NULL DEFAULT '',
                    secret     TEXT NOT NULL DEFAULT '',
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                )
            """)
            # The composite key is the whole point: without account_id in
            # it, two accounts collide on a shared chat_id.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS im_bindings (
                    account_id TEXT NOT NULL,
                    chat_id    TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (account_id, chat_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS im_outbox (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id   TEXT NOT NULL,
                    chat_id      TEXT NOT NULL,
                    text         TEXT NOT NULL,
                    created_at   REAL NOT NULL,
                    delivered_at REAL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS im_outbox_pending"
                " ON im_outbox (account_id, delivered_at, id)")
            # Pairings are short-lived by design: a row that is minutes old is
            # worthless, so they are expired on every read rather than kept.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS im_pairings (
                    pairing_id   TEXT PRIMARY KEY,
                    secret       TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    created_at   REAL NOT NULL,
                    expires_at   REAL NOT NULL,
                    connector    TEXT NOT NULL DEFAULT '',
                    account_id   TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    qr           BLOB,
                    delivered    INTEGER NOT NULL DEFAULT 0
                )
            """)
            await db.commit()

    # ---------------------------------------------------------------- accounts

    async def upsert_account(self, account_id: str, name: str = "",
                             secret: str = "",
                             enabled: bool = True) -> IMAccount:
        account_id = validate_account_id(account_id)
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO im_accounts"
                " (account_id, name, secret, enabled, created_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(account_id) DO UPDATE SET"
                "  name = excluded.name,"
                "  secret = CASE WHEN excluded.secret = ''"
                "                THEN im_accounts.secret"
                "                ELSE excluded.secret END,"
                "  enabled = excluded.enabled",
                (account_id, name, secret, 1 if enabled else 0, now),
            )
            await db.commit()
            row = await self._account_row(db, account_id)
        if row is None:  # pragma: no cover - the insert above guarantees one
            raise IMAccountError(f"account {account_id!r} disappeared")
        return self._to_account(row)

    async def _account_row(self, db: aiosqlite.Connection,
                           account_id: str) -> Optional[Any]:
        cursor = await db.execute(
            "SELECT account_id, name, enabled, created_at FROM im_accounts"
            " WHERE account_id = ?", (account_id,))
        return await cursor.fetchone()

    @staticmethod
    def _to_account(row: Any) -> IMAccount:
        return IMAccount(account_id=row[0], name=row[1] or "",
                         enabled=bool(row[2]), created_at=row[3] or 0.0)

    async def list_accounts(self) -> List[IMAccount]:
        """All accounts, without their secrets.

        The select list is explicit on purpose: ``SELECT *`` here would
        put a secret in whatever a caller decides to serialise.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT account_id, name, enabled, created_at"
                " FROM im_accounts ORDER BY created_at DESC")
            rows = await cursor.fetchall()
        return [self._to_account(r) for r in rows]

    async def get_account(self, account_id: str) -> Optional[IMAccount]:
        async with aiosqlite.connect(self.db_path) as db:
            row = await self._account_row(db, account_id)
        return self._to_account(row) if row else None

    async def get_secret(self, account_id: str) -> Optional[str]:
        """The inbound signing secret for one account, or None.

        A separate call from :meth:`get_account` so that a caller listing
        accounts cannot accidentally hold a secret it never asked for.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT secret FROM im_accounts WHERE account_id = ?",
                (account_id,))
            row = await cursor.fetchone()
        return (row[0] or None) if row else None

    async def delete_account(self, account_id: str) -> None:
        """Remove an account and everything scoped to it."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM im_accounts WHERE account_id = ?",
                             (account_id,))
            await db.execute("DELETE FROM im_bindings WHERE account_id = ?",
                             (account_id,))
            await db.execute("DELETE FROM im_outbox WHERE account_id = ?",
                             (account_id,))
            await db.commit()

    # ---------------------------------------------------------------- bindings

    async def bind(self, account_id: str, chat_id: str,
                   project_id: str) -> None:
        account_id = validate_account_id(account_id)
        if not (chat_id or "").strip():
            raise IMAccountError("chat_id must not be empty")
        if not (project_id or "").strip():
            raise IMAccountError("project_id must not be empty")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO im_bindings"
                " (account_id, chat_id, project_id, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(account_id, chat_id) DO UPDATE SET"
                "  project_id = excluded.project_id,"
                "  updated_at = excluded.updated_at",
                (account_id, chat_id, project_id, time.time()),
            )
            await db.commit()

    async def lookup(self, account_id: str, chat_id: str) -> Optional[str]:
        """The project bound to one conversation on one account."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT project_id FROM im_bindings"
                " WHERE account_id = ? AND chat_id = ?",
                (account_id, chat_id))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def list_bindings(
            self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            if account_id is None:
                cursor = await db.execute(
                    "SELECT account_id, chat_id, project_id, updated_at"
                    " FROM im_bindings ORDER BY updated_at DESC")
            else:
                cursor = await db.execute(
                    "SELECT account_id, chat_id, project_id, updated_at"
                    " FROM im_bindings WHERE account_id = ?"
                    " ORDER BY updated_at DESC",
                    (validate_account_id(account_id),))
            rows = await cursor.fetchall()
        return [{"account_id": r[0], "chat_id": r[1], "project_id": r[2],
                 "updated_at": r[3]} for r in rows]

    async def unbind(self, account_id: str, chat_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM im_bindings WHERE account_id = ? AND chat_id = ?",
                (account_id, chat_id))
            await db.commit()

    # ------------------------------------------------------------------ outbox

    async def enqueue(self, account_id: str, chat_id: str,
                      text: str) -> int:
        """Queue one reply for the connector that owns this account."""
        if not (text or "").strip():
            raise IMAccountError("refusing to queue an empty reply")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO im_outbox"
                " (account_id, chat_id, text, created_at) VALUES (?, ?, ?, ?)",
                (validate_account_id(account_id), chat_id, text,
                 time.time()))
            await db.commit()
            return int(cursor.lastrowid or 0)

    async def pending(self, account_id: str,
                      limit: int = 50) -> List[OutboundMessage]:
        """Undelivered replies for **this** account, oldest first.

        Filtering by ``account_id`` is what keeps the connectors from
        seeing each other's traffic. Delivery is at-least-once: a
        connector that dies mid-send re-collects the message, because
        losing a reply is worse than repeating one.
        """
        limit = max(1, min(int(limit), 500))
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id, account_id, chat_id, text, created_at"
                " FROM im_outbox WHERE account_id = ? AND delivered_at IS NULL"
                " ORDER BY id ASC LIMIT ?",
                (validate_account_id(account_id), limit))
            rows = await cursor.fetchall()
        return [OutboundMessage(id=r[0], account_id=r[1], chat_id=r[2],
                                text=r[3], created_at=r[4]) for r in rows]

    async def ack(self, account_id: str, ids: List[int]) -> int:
        """Mark replies delivered, after the connector has sent them.

        Scoped to one account on purpose: an ack that only took ids would
        let one connector mark another account's replies as delivered --
        silently dropping mail that was never sent.
        """
        clean = [int(i) for i in ids if int(i) > 0]
        if not clean:
            return 0
        placeholders = ",".join("?" for _ in clean)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE im_outbox SET delivered_at = ?"
                f" WHERE account_id = ?"
                f" AND id IN ({placeholders}) AND delivered_at IS NULL",
                [time.time(), validate_account_id(account_id), *clean])
            await db.commit()
            return int(cursor.rowcount or 0)

    async def pending_count(self, account_id: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM im_outbox"
                " WHERE account_id = ? AND delivered_at IS NULL",
                (validate_account_id(account_id),))
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


    # ---------------------------------------------------------------- pairing

    PAIRING_TTL_SECONDS = 300

    def _pairing_from_row(self, row: Any) -> Pairing:
        return Pairing(
            pairing_id=row[0], secret=row[1], status=row[2],
            created_at=float(row[3]), expires_at=float(row[4]),
            connector=row[5] or "", account_id=row[6] or "",
            display_name=row[7] or "", has_qr=row[8] is not None)

    PAIRING_COLUMNS = ("pairing_id, secret, status, created_at, expires_at,"
                       " connector, account_id, display_name, qr")

    async def _expire_pairings(self, db: aiosqlite.Connection,
                               now: float | None = None) -> None:
        await db.execute(
            "UPDATE im_pairings SET status = 'expired'"
            " WHERE expires_at < ? AND status NOT IN ('bound', 'expired')",
            (time.time() if now is None else now,))

    async def create_pairing(self, ttl_seconds: int | None = None) -> Pairing:
        ttl = self.PAIRING_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        now = time.time()
        pairing = Pairing(
            pairing_id=secrets.token_hex(6), secret=secrets.token_hex(16),
            status="waiting", created_at=now, expires_at=now + ttl)
        async with aiosqlite.connect(self.db_path) as db:
            await self._expire_pairings(db, now)
            await db.execute(
                "INSERT INTO im_pairings (pairing_id, secret, status,"
                " created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (pairing.pairing_id, pairing.secret, pairing.status, now,
                 pairing.expires_at))
            await db.commit()
        return pairing

    async def get_pairing(self, pairing_id: str) -> Optional[Pairing]:
        async with aiosqlite.connect(self.db_path) as db:
            await self._expire_pairings(db)
            await db.commit()
            cursor = await db.execute(
                f"SELECT {self.PAIRING_COLUMNS} FROM im_pairings"
                " WHERE pairing_id = ?", (pairing_id,))
            row = await cursor.fetchone()
        return self._pairing_from_row(row) if row else None

    async def claim_pairing(self, connector: str = "") -> Optional[Pairing]:
        """Hand the oldest unclaimed pairing to a connector.

        Claiming is the connector's first act, so it is the one call that
        cannot present the pairing secret -- it does not know it yet. It is
        therefore as narrow as possible: it only ever returns a pairing that is
        still waiting, and it marks it claimed in the same transaction, so two
        connectors cannot take the same code.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await self._expire_pairings(db)
            cursor = await db.execute(
                f"SELECT {self.PAIRING_COLUMNS} FROM im_pairings"
                " WHERE status = 'waiting' ORDER BY created_at LIMIT 1")
            row = await cursor.fetchone()
            if not row:
                await db.commit()
                return None
            await db.execute(
                "UPDATE im_pairings SET status = 'claimed', connector = ?"
                " WHERE pairing_id = ? AND status = 'waiting'",
                (connector, row[0]))
            await db.commit()
        return self._pairing_from_row(row)

    async def _pairing_write(self, pairing_id: str, secret: str,
                             sql: str, params: list) -> bool:
        """Any connector write requires the pairing's own secret."""
        pairing = await self.get_pairing(pairing_id)
        if pairing is None:
            return False
        if not hmac.compare_digest(pairing.secret, secret or ""):
            return False
        if pairing.expired:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                sql.replace("WHERE pairing_id = ?",
                            "WHERE pairing_id = ? AND secret = ?"),
                [*params, pairing_id, pairing.secret])
            await db.commit()
            return int(cursor.rowcount or 0) > 0

    async def set_pairing_qr(self, pairing_id: str, secret: str,
                             png: bytes) -> bool:
        return await self._pairing_write(
            pairing_id, secret,
            "UPDATE im_pairings SET qr = ?, status = 'qr' WHERE pairing_id = ?",
            [png])

    async def set_pairing_status(self, pairing_id: str, secret: str,
                                 status: str) -> bool:
        if status not in ("scanned", "claimed"):
            raise IMAccountError(f"unusable pairing status: {status}")
        return await self._pairing_write(
            pairing_id, secret,
            "UPDATE im_pairings SET status = ? WHERE pairing_id = ?",
            [status])

    async def pairing_qr(self, pairing_id: str) -> Optional[bytes]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT qr FROM im_pairings WHERE pairing_id = ?", (pairing_id,))
            row = await cursor.fetchone()
        return bytes(row[0]) if row and row[0] is not None else None

    async def confirm_pairing(self, pairing_id: str, secret: str,
                              account_id: str,
                              display_name: str = "") -> Optional[str]:
        """Finish the handshake: create the account and return its secret once.

        Returns None when the pairing is unknown, expired, already used, or the
        account id is not usable -- the caller turns that into a 4xx without
        learning anything else.
        """
        account_id = validate_account_id(account_id)
        pairing = await self.get_pairing(pairing_id)
        if pairing is None or pairing.expired or pairing.status == "bound":
            return None
        if not hmac.compare_digest(pairing.secret, secret or ""):
            return None
        account_secret = secrets.token_hex(16)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE im_pairings SET status = 'bound', account_id = ?,"
                " display_name = ?, delivered = 1"
                " WHERE pairing_id = ? AND secret = ? AND status != 'bound'",
                (account_id, display_name, pairing_id, pairing.secret))
            await db.commit()
            if int(cursor.rowcount or 0) == 0:
                return None
        await self.upsert_account(account_id, name=display_name,
                                 secret=account_secret, enabled=True)
        return account_secret

    async def delete_pairing(self, pairing_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM im_pairings WHERE pairing_id = ?",
                             (pairing_id,))
            await db.commit()
