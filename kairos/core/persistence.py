"""SQLite-backed persistence for Kairos projects and messages."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional


class Persistence:
    """SQLite persistence for projects, messages, and requirements."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Migrate FIRST so _init_schema's CREATE INDEX works on the
        # upgraded schema. (CREATE TABLE IF NOT EXISTS is a no-op when the
        # table already exists, so on legacy DBs we still need the ALTER.)
        self._migrate()
        self._init_schema()

    def _migrate(self):
        """Lightweight additive migrations for older databases.

        SQLite's `CREATE TABLE IF NOT EXISTS` won't add columns to an
        existing table, so we ALTER here. Wrapped in try/except since
        these are idempotent — re-running is safe on fresh DBs.
        """
        with sqlite3.connect(self.db_path) as conn:
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "project_id" not in existing:
                try:
                    conn.execute("ALTER TABLE messages ADD COLUMN project_id TEXT")
                except sqlite3.OperationalError:
                    pass
            # loop_rounds.insert_order preserves wall-clock insertion order
            # even when multiple rows share the same created_at (which
            # time.time() happily does in fast unit tests). Sort uses it
            # as the tiebreaker after created_at + round.
            loop_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(loop_rounds)").fetchall()
            }
            if "insert_order" not in loop_cols:
                try:
                    conn.execute("ALTER TABLE loop_rounds ADD COLUMN insert_order INTEGER")
                except sqlite3.OperationalError:
                    pass

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    workspace TEXT,
                    work_dir TEXT,
                    requirements TEXT,
                    status TEXT,
                    created_at REAL,
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    sender TEXT,
                    receiver TEXT,
                    topic TEXT,
                    content TEXT,
                    msg_type TEXT,
                    timestamp REAL,
                    metadata TEXT,
                    project_id TEXT
                );

                -- Structured per-round digest of a LoopReview loop. We keep
                -- these separately from the raw messages table because
                -- they're what we inject back into the next loop's prompt
                -- (the digest is what the Coder/Reviewer care about; raw
                -- tool traces are noise).
                --
                -- The primary key is (project_id, session_id, round) so
                -- re-saving the same round is idempotent (INSERT OR REPLACE
                -- works as intended, instead of appending duplicate rows).
                CREATE TABLE IF NOT EXISTS loop_rounds (
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    coder_summary TEXT,
                    review_summary TEXT,
                    review_json TEXT,
                    score INTEGER,
                    approve INTEGER,
                    created_at REAL,
                    insert_order INTEGER,
                    PRIMARY KEY (project_id, session_id, round)
                );

                -- Reference files uploaded to a project (PDF, markdown,
                -- datasheet, etc.). The full content is stored inline so
                -- the Coder can see it without touching the filesystem;
                -- the orchestrator injects a digest of these into the
                -- loop's starting prompt. We don't index `content` (FTS5
                -- could be added later) — list/load is by project_id.
                CREATE TABLE IF NOT EXISTS project_files (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mime TEXT,
                    size INTEGER,
                    content TEXT,
                    uploaded_at REAL
                );

                -- Inline review comments (one per round, JSON blob).
                -- UI / editor plugins fetch this and render ::code-comment
                -- directives. Capped at last 200 rounds per project to
                -- keep the table small.
                CREATE TABLE IF NOT EXISTS review_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    comments_json TEXT NOT NULL,
                    created_at REAL,
                    UNIQUE(project_id, round)
                );

                -- Reviewer ask-human history. Each row is one question
                -- + (optional) user answer. Lets the UI show "the
                -- reviewer asked 3 questions during this loop".
                CREATE TABLE IF NOT EXISTS ask_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    context TEXT,
                    answer TEXT,
                    asked_at REAL,
                    answered_at REAL
                );

                -- Loop checkpoints: durable record of every auto-commit
                -- the loop made. Mirrors git but queryable without
                -- spawning a subprocess. Useful for the rollback UI
                -- when the workspace has been wiped.
                -- Per-project style/rule preferences. Each rule is a
                -- short freeform text the user wants the Coder to always
                -- follow ("use type hints", "no print debugging"). The
                -- orchestrator injects the active rules into the Coder
                -- system prompt on the next round. We store as one row
                -- per rule so a future UI can edit/delete them.
                CREATE TABLE IF NOT EXISTS project_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS loop_checkpoints (
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    sha TEXT NOT NULL,
                    score INTEGER,
                    approved INTEGER,
                    summary TEXT,
                    created_at REAL,
                    PRIMARY KEY (project_id, session_id, round)
                );
            """)
            # Indexes are created after the table is guaranteed to have
            # the column (via _migrate). CREATE INDEX IF NOT EXISTS is
            # safe to run on every boot.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_project ON messages(project_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_loop_project_session "
                "ON loop_rounds(project_id, session_id, round)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_project ON review_comments(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ask_project ON ask_history(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_project ON loop_checkpoints(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prefs_project ON project_preferences(project_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_project "
                "ON project_files(project_id)"
            )


    # ----------------------------------------------------------------- preferences

    def add_preference(self, project_id: str, kind: str, rule: str) -> int:
        """Insert a style/rule preference for a project.

        `kind` is "always" / "never" / "prefer" - used by the UI to group
        rules and by the prompt builder to phrase the rule.
        Returns the new row id.
        """
        if kind not in ("always", "never", "prefer"):
            kind = "always"
        rule = rule.strip()
        if not rule:
            raise ValueError("rule cannot be empty")
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO project_preferences (project_id, kind, rule, created_at) "
                "VALUES (?, ?, ?, ?)",
                (project_id, kind, rule, time.time()),
            )
            return cur.lastrowid

    def delete_preference(self, project_id: str, pref_id: int) -> bool:
        """Delete a preference by id (only if it belongs to the project)."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM project_preferences WHERE id = ? AND project_id = ?",
                (pref_id, project_id),
            )
            return cur.rowcount > 0

    def list_preferences(self, project_id: str) -> list:
        """List all preferences for a project, oldest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM project_preferences WHERE project_id = ? "
                "ORDER BY created_at ASC, id ASC",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    def save_project(self, project):
        """Save or update a project."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO projects
                (id, name, description, workspace, work_dir, requirements, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project.id, project.name, project.description,
                str(project.workspace), project.work_dir, project.requirements,
                project.status, project.created_at, time.time(),
            ))

    def load_projects(self) -> List[dict]:
        """Load all projects."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]

    def delete_project(self, project_id: str):
        """Delete a project."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            # Cascade: remove its reference files too.
            conn.execute("DELETE FROM project_files WHERE project_id = ?", (project_id,))

    # ------------------------------------------------------------------ Project files
    #
    # Reference files uploaded by the user (PDFs, datasheets, design notes,
    # …). Full content is stored inline so the orchestrator can inject a
    # digest into the Coder prompt without re-reading the filesystem.

    def add_file(self, file_id: str, project_id: str, name: str,
                  mime: str, size: int, content: str) -> None:
        """Insert one reference file. `content` may be text, base64 of
        binary, or any string — we don't try to be clever here."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO project_files
                (id, project_id, name, mime, size, content, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                file_id, project_id, name, mime or "application/octet-stream",
                int(size or 0), content or "", time.time(),
            ))

    def list_files(self, project_id: str) -> List[dict]:
        """Return all reference files for a project, newest first.

        Note: `content` is loaded lazily in `load_file()` so the list
        endpoint stays light. The list view returns metadata only; the
        Coder's prompt digest is built by `orchestrator.get_reference_digest`.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, project_id, name, mime, size, uploaded_at
                FROM project_files WHERE project_id = ?
                ORDER BY uploaded_at DESC
            """, (project_id,)).fetchall()
            return [dict(r) for r in rows]

    def load_file(self, file_id: str) -> Optional[dict]:
        """Return one reference file (including its content) by id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM project_files WHERE id = ?", (file_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_file(self, file_id: str) -> bool:
        """Delete one reference file. Returns True if a row was removed."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM project_files WHERE id = ?", (file_id,))
            return cur.rowcount > 0

    def load_all_files_for_project(self, project_id: str) -> List[dict]:
        """Return ALL reference files for a project, including content.
        Used by the orchestrator to build the Coder's prompt digest."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, name, mime, size, content
                FROM project_files WHERE project_id = ?
                ORDER BY uploaded_at ASC
            """, (project_id,)).fetchall()
            return [dict(r) for r in rows]

    def save_message(self, msg):
        """Save a message."""
        # Resolve project_id from metadata so per-project queries work
        # (D-01). Falls back to deriving from sender/receiver for older
        # callers that embed "<project_id>.<role>" in those fields.
        project_id = (msg.metadata or {}).get("project_id", "")
        if not project_id and msg.sender.startswith("user") is False:
            # sender like "<project_id>.team_leader" — extract.
            if "." in msg.sender:
                project_id = msg.sender.split(".", 1)[0]
        if not project_id and msg.receiver and "." in msg.receiver:
            project_id = msg.receiver.split(".", 1)[0]
        with sqlite3.connect(self.db_path) as conn:
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            metadata = json.dumps(msg.metadata, ensure_ascii=False) if msg.metadata else "{}"
            conn.execute("""
                INSERT OR REPLACE INTO messages
                (id, sender, receiver, topic, content, msg_type, timestamp, metadata, project_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg.id, msg.sender, msg.receiver, msg.topic,
                content, msg.msg_type, msg.timestamp, metadata, project_id,
            ))

    def load_messages(self, limit: int = 100, project_id: Optional[str] = None) -> List[dict]:
        """Load recent messages, optionally scoped to a single project."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if project_id is None:
                rows = conn.execute(
                    "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE project_id = ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------ Loop memory
    #
    # Cross-round / cross-session memory. Each round of a LoopReview loop
    # writes a digest here so the NEXT loop on the same project can recall
    # what was tried, what got approved, and what got stuck.

    def save_loop_round(self, project_id: str, session_id: str, round_no: int,
                         coder_summary: str, review: dict) -> None:
        """Persist one round's structured digest. Idempotent on
        (project_id, session_id, round) — if you save twice, the row is
        overwritten (the table's PRIMARY KEY makes INSERT OR REPLACE work)."""
        review_json = json.dumps(review, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            # Monotonic insert counter so load_loop_rounds can preserve
            # wall-clock order even when time.time() returns identical
            # values for rows saved in the same microsecond.
            insert_order = self._next_loop_insert_order(conn)
            conn.execute(
                "INSERT OR REPLACE INTO loop_rounds "
                "(project_id, session_id, round, coder_summary, review_summary, "
                "review_json, score, approve, created_at, insert_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id, session_id, round_no,
                    coder_summary[:5000],
                    (review.get("summary") or "")[:2000],
                    review_json,
                    int(review.get("score") or 0),
                    1 if review.get("approve") else 0,
                    time.time(),
                    insert_order,
                ),
            )

    def _next_loop_insert_order(self, conn) -> int:
        """Return a monotonically-increasing integer used as the
        wall-clock tiebreaker when sorting rounds. We compute it from
        MAX(insert_order)+1 (or 1 if empty) so each connection observes
        the same sequence even when multiple processes write at once.
        """
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(insert_order), 0) FROM loop_rounds"
            ).fetchone()
            return int(row[0] or 0) + 1
        except sqlite3.OperationalError:
            # Pre-migration DB without the insert_order column.
            return int(time.time() * 1000)

    def load_loop_rounds(self, project_id: str, limit: int = 20) -> List[dict]:
        """Load recent loop rounds for a project, oldest first.

        Sort key is (created_at, insert_order) ASC. The insert_order
        column is a per-row monotonic counter set by save_loop_round,
        so when many rows share the same created_at (fast unit tests,
        batch saves, or even an out-of-order round save) we still get
        them back in the order they were actually inserted. Newer
        rounds always come last, which is what the Coder wants — it
        reads "R1 happened, R2 happened, now I'm R3".

        We load more than `limit` and trim at the END so the most recent
        rounds are preserved (the ones the Coder cares about most).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM loop_rounds WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        # Sort key preserves wall-clock order even when many rows share
        # the same time.time() value (fast unit tests, batch saves).
        # insert_order is the per-insert monotonic counter set by
        # save_loop_round; it ties after created_at and round.
        def _sort_key(r):
            # insert_order is a per-row monotonic counter set by
            # save_loop_round; it captures wall-clock insertion order
            # even when time.time() returns identical values. We use
            # it as the final tiebreaker so out-of-order round numbers
            # (a session that saved R3 before R1 by accident) come back
            # in the order they were actually inserted.
            return (
                r.get("created_at") or 0,
                r.get("insert_order") or 0,
            )
        ordered = sorted([dict(r) for r in rows], key=_sort_key)
        return ordered[:limit]

    def load_last_loop_summary(self, project_id: str) -> Optional[str]:
        """One-line digest of the most recent loop's last round — useful
        for the UI to show "last time this project..." without rehydrating
        full history."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT round, score, approve, review_summary
                FROM loop_rounds
                WHERE project_id = ?
                ORDER BY created_at DESC, round DESC
                LIMIT 1
            """, (project_id,)).fetchone()
        if not row:
            return None
        round_no, score, approve, summary = row
        verdict = "approved" if approve else "rejected"
        return f"R{round_no} {verdict} (score {score}): {summary[:150]}"

    def delete_loop_rounds(self, project_id: str) -> None:
        """When a project is deleted, clean up its loop memory."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM loop_rounds WHERE project_id = ?", (project_id,))

    # ---------------------------------------------------------- review_comments
    def save_review_comments(self, project_id: str, round_no: int,
                              comments: list) -> None:
        """Persist a round's inline review comments. Idempotent on
        (project_id, round) — INSERT OR REPLACE makes re-saving safe
        (the reviewer may emit a slightly different verdict on retry).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO review_comments
                (project_id, round, comments_json, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                project_id, round_no,
                json.dumps(comments, ensure_ascii=False),
                time.time(),
            ))

    def load_review_comments(self, project_id: str,
                              round_no: int = 0) -> list:
        """Load comments for a specific round (round=0 = latest).

        Returns [] if nothing stored yet.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if round_no == 0:
                row = conn.execute("""
                    SELECT comments_json FROM review_comments
                    WHERE project_id = ?
                    ORDER BY round DESC LIMIT 1
                """, (project_id,)).fetchone()
            else:
                row = conn.execute("""
                    SELECT comments_json FROM review_comments
                    WHERE project_id = ? AND round = ?
                """, (project_id, round_no)).fetchone()
        if not row:
            return []
        try:
            return json.loads(row["comments_json"])
        except (json.JSONDecodeError, TypeError):
            return []

    # ---------------------------------------------------------- ask_history
    def record_ask(self, project_id: str, round_no: int,
                    question: str, context: str = "") -> int:
        """Persist a Reviewer ask_human. Returns the ask row id so the
        caller can later match it to an answer."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                INSERT INTO ask_history
                (project_id, round, question, context, asked_at)
                VALUES (?, ?, ?, ?, ?)
            """, (project_id, round_no, question, context[:1000], time.time()))
            return cur.lastrowid or 0

    def record_ask_answer(self, ask_id: int, answer: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE ask_history SET answer = ?, answered_at = ?
                WHERE id = ?
            """, (answer, time.time(), ask_id))

    def load_ask_history(self, project_id: str, limit: int = 50) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM ask_history
                WHERE project_id = ?
                ORDER BY asked_at DESC LIMIT ?
            """, (project_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------- checkpoints
    def save_checkpoint(self, project_id: str, session_id: str,
                         round_no: int, sha: str, score: int,
                         approved: bool, summary: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO loop_checkpoints
                (project_id, session_id, round, sha, score, approved, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project_id, session_id, round_no, sha, score,
                1 if approved else 0, summary[:500], time.time(),
            ))

    def load_checkpoints(self, project_id: str, limit: int = 100) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT round, sha, score, approved, summary, created_at
                FROM loop_checkpoints
                WHERE project_id = ?
                ORDER BY round DESC LIMIT ?
            """, (project_id, limit)).fetchall()
        return [
            {
                "round": r["round"],
                "sha": r["sha"],
                "score": r["score"] or 0,
                "approved": bool(r["approved"]),
                "summary": r["summary"] or "",
                "ts": r["created_at"] or 0,
            }
            for r in rows
        ]

    def delete_project_memory(self, project_id: str) -> None:
        """Wipe every per-project table when the project is deleted."""
        with sqlite3.connect(self.db_path) as conn:
            for tbl in ("messages", "loop_rounds", "project_files",
                         "review_comments", "ask_history", "loop_checkpoints", "project_preferences"):
                conn.execute(f"DELETE FROM {tbl} WHERE project_id = ?",
                             (project_id,))
