"""HistorySearchTool — the agent searches the *other* sessions' stored messages.

Every Kairos session's conversation is persisted in ``kairos.db`` (the
``messages`` table), but nothing on the chat path could reach it: asked
「上次我们是怎么做的」, the Coder could only answer "you have this session".
This tool is the reader. It opens the database **read-only**
(``file:...?mode=ro`` — the SQLite engine itself refuses a write) and searches
the messages of the sessions *other than the current one*.

Safety, on purpose:

* Only two tables are ever named in a query: ``messages`` and ``projects``
  (the latter only for the session title). Any other table — including a
  settings table that may hold credentials — is refused by name and never
  opened. If ``messages``/``projects`` are absent the tool says so instead of
  guessing at another schema.
* The current session (the tool's own ``project_id``) is excluded from every
  result, so it can only ever surface *past* work.
* The output is capped: at most 8 hits, 400 characters per snippet, 6000
  characters in total. When there are more hits the model is told to narrow
  the keyword.
* Any credential-shaped run is masked before it can reach the model — a past
  message may have had a key pasted into it.
* A missing, locked or schema-less database returns one clear Chinese sentence
  rather than raising.

This is a small, self-contained reader; it deliberately does not import the
write path (``kairos.core.persistence``) so it can never mutate the DB.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, List, Optional

from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

#: The ONLY tables this tool may read. ``messages`` holds the conversation,
#: ``projects`` supplies the session title. Everything else — notably a
#: settings/key table — is off limits, by name.
ALLOWED_TABLES = ("messages", "projects")

#: Conversation topics worth searching. The high-volume *process* rows
#: (``stream.chunk`` fires once per token) are deliberately excluded: they are
#: noise, not something the user "said last time".
SEARCH_TOPICS = (
    "user.chat", "agent.chat", "agent.chat_reply", "agent.message",
    "agent.response", "user.input", "ask.answer",
)

#: Output caps (the tool must never flood the model's context).
MAX_RESULTS = 8
DEFAULT_RESULTS = 5
MAX_SNIPPET = 400
MAX_TOTAL = 6000

#: One sentence for every "cannot search" state, so the model reports it plainly.
UNAVAILABLE = (
    "历史检索不可用：没有找到可读取的会话数据库（kairos.db）。"
    "你只能看到当前会话；如果用户需要过去的内容，请他把相关片段贴进来。"
)

#: Credential-shaped runs are masked before they reach the model. A real key
#: pasted into an old message must never be echoed back by a search.
_SECRET_RE = re.compile(
    r"(?:sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|xox[abpros]-|AIza|AKIA|hf_)"
    r"[A-Za-z0-9_\-]{8,}"
)


def _redact(text: str) -> str:
    """Mask anything that looks like a credential."""
    return _SECRET_RE.sub("[REDACTED]", text or "")


def _resolve_db_path() -> Optional[Path]:
    """Resolve the app's ``kairos.db`` the same way the rest of the app does.

    ``gate_report.default_db_path`` is the repo's single resolver
    (``KAIROS_DATA_DIR`` → ``settings.data_dir`` → repo ``data/``), so the
    search reads exactly the database the app writes.
    """
    try:
        from kairos.gate_report import default_db_path
        return Path(default_db_path())
    except Exception:  # noqa: BLE001 — fall back to the raw convention
        env = os.environ.get("KAIROS_DATA_DIR")
        if env:
            return Path(env) / "kairos.db"
        try:
            from kairos.config.settings import settings
            return Path(settings.data_dir) / "kairos.db"
        except Exception:  # noqa: BLE001
            return Path(__file__).resolve().parent.parent.parent / "data" / "kairos.db"


def _open_readonly(db_path: "Path | str") -> sqlite3.Connection:
    """Open SQLite read-only via a ``file:...?mode=ro`` URI.

    The engine refuses any write (``CREATE``/``INSERT``/``UPDATE``/``DELETE``)
    with ``sqlite3.OperationalError`` — the read-only guarantee is enforced by
    SQLite, not by this function.
    """
    uri = "file:{}?mode=ro".format(Path(db_path).resolve().as_posix())
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so the query is matched literally."""
    return (value.replace("\\", "\\\\")
                 .replace("%", "\\%")
                 .replace("_", "\\_"))


def _coerce_limit(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_RESULTS
    if n < 1:
        n = 1
    return min(n, MAX_RESULTS)


def _fmt_time(ts: Any) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "时间未知"


def _snippet(content: str, query: str) -> str:
    """A redacted, whitespace-flattened excerpt around the first match."""
    text = _redact(content or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_SNIPPET:
        return text
    idx = text.lower().find((query or "").lower())
    if idx < 0:
        return text[:MAX_SNIPPET] + "…"
    half = MAX_SNIPPET // 2
    start = max(0, idx - half)
    end = min(len(text), start + MAX_SNIPPET)
    start = max(0, end - MAX_SNIPPET)
    out = text[start:end]
    if start > 0:
        out = "…" + out
    if end < len(text):
        out = out + "…"
    return out


class HistorySearchTool(BaseTool):
    """Search the messages of past Kairos sessions, quoted with their source."""

    name = "history_search"
    description = (
        "Search the user's PAST Kairos sessions (the stored conversation "
        "messages) for a keyword or phrase and quote the matching excerpts "
        "with their session title, id, time and topic. Use this when the user "
        "refers to something you cannot see — 「之前 / 上次 / 历史 / you said "
        "earlier / how did we do X before」 — instead of saying you only have "
        "the current session. The current session is always excluded (this "
        "tool only reaches *other* sessions). Results are capped at 8 hits "
        "with short snippets; when there are more hits, narrow the keyword. "
        "Read-only: it never modifies the database."
    )

    def __init__(self, allowed_root: "str | Path" = ".",
                 project_id: str = "", db_path: "Optional[Path]" = None):
        super().__init__(allowed_root=allowed_root)
        # The session the tool is running inside — excluded from every result.
        self._project_id = project_id or ""
        # Injectable for tests / non-default deployments; resolved lazily.
        self._db_path = Path(db_path) if db_path else None

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Keyword or phrase to look for in past session "
                            "messages (literal substring match, not regex)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of hits to return. Default 5, "
                            "hard cap 8."
                        ),
                    },
                    "session": {
                        "type": "string",
                        "description": (
                            "Optional: restrict the search to one past "
                            "session id. Omit to search all past sessions."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }

    # -- internals ---------------------------------------------------------

    def _query(self, conn: sqlite3.Connection, query: str, limit: int,
               session: Any) -> List[dict]:
        """Run the (whitelisted, parameterised) search.

        Only ``messages`` and ``projects`` are referenced. One extra row is
        fetched so the caller can tell "there are more" from "that was all".
        """
        sql = (
            "SELECT m.id AS id, m.project_id AS project_id, m.topic AS topic, "
            "m.sender AS sender, m.timestamp AS timestamp, "
            "m.content AS content, p.name AS session_title "
            "FROM messages AS m "
            "LEFT JOIN projects AS p ON p.id = m.project_id "
            "WHERE m.content LIKE ? ESCAPE '\\' "
        )
        params: List[Any] = ["%" + _escape_like(query) + "%"]
        sql += "AND m.topic IN (%s) " % ",".join("?" * len(SEARCH_TOPICS))
        params.extend(SEARCH_TOPICS)
        if self._project_id:
            sql += "AND (m.project_id IS NULL OR m.project_id != ?) "
            params.append(self._project_id)
        sess = str(session).strip() if session else ""
        if sess:
            sql += "AND m.project_id = ? "
            params.append(sess)
        sql += "ORDER BY m.timestamp DESC, m.id DESC LIMIT ?"
        params.append(limit + 1)
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    def _render(self, query: str, rows: List[dict], limit: int) -> ToolResult:
        more = len(rows) > limit
        rows = rows[:limit]
        if not rows:
            return ToolResult(
                success=True,
                output=(
                    f'没有在过去会话里找到包含「{query}」的消息。'
                    f'（只检索会话消息，且已排除当前会话；换一个关键词或更'
                    f'宽的说法再试。）'
                ),
                metadata={"hits": 0, "more": False},
            )

        lines = [f'在过去会话里找到 {len(rows)} 条含「{query}」的消息（已排除当前会话）：']
        used = len(lines[0])
        shown = 0
        for i, r in enumerate(rows, 1):
            title = (r.get("session_title") or "").strip() or "未命名"
            pid = r.get("project_id") or "—"
            topic = r.get("topic") or "—"
            block = (
                f"[{i}] 会话「{title}」(id={pid}) · {_fmt_time(r.get('timestamp'))}"
                f" · {topic}\n"
                f"    {_snippet(r.get('content') or '', query)}\n"
            )
            if used + len(block) > MAX_TOTAL:
                break
            lines.append(block)
            used += len(block)
            shown += 1

        truncated = shown < len(rows)
        if more or truncated:
            lines.append(
                "（命中不止这些——请缩小关键词（更具体的词），"
                "或用 session 限定到某个会话 id。）"
            )
        return ToolResult(
            success=True,
            output="\n".join(lines).rstrip(),
            metadata={"hits": shown, "more": bool(more or truncated)},
        )

    def _run(self, query: Any, limit: Any, session: Any) -> ToolResult:
        q = str(query or "").strip()
        if not q:
            return ToolResult(
                success=True,
                output='请给出要检索的关键词，例如 history_search(query="登录")。',
            )
        n = _coerce_limit(limit)

        db_path = self._db_path or _resolve_db_path()
        if db_path is None or not Path(db_path).exists():
            return ToolResult(success=True, output=UNAVAILABLE,
                              metadata={"available": False})

        conn = None
        try:
            conn = _open_readonly(db_path)
            # Whitelist check: if the expected session tables are not present,
            # do not probe any other table — just report unavailability.
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if not set(ALLOWED_TABLES).issubset(names):
                return ToolResult(success=True, output=UNAVAILABLE,
                                  metadata={"available": False})
            rows = self._query(conn, q, n, session)
        except sqlite3.Error as exc:
            # Missing / locked / corrupt / unmigrated — all non-fatal.
            logger.debug("history_search unavailable: %s", exc)
            return ToolResult(success=True, output=UNAVAILABLE,
                              metadata={"available": False})
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

        return self._render(q, rows, n)

    async def execute(self, query: str = "", limit: Any = DEFAULT_RESULTS,
                      session: Any = None, **kwargs: Any) -> ToolResult:
        try:
            return self._run(query, limit, session)
        except Exception as exc:  # noqa: BLE001 — a tool must never raise
            logger.debug("history_search failed: %s", exc, exc_info=True)
            return ToolResult(
                success=False, output="",
                error=f"历史检索出错：{exc}",
            )
