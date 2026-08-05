"""SQLite-backed persistence for Kairos projects and messages."""
from __future__ import annotations
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class Persistence:
    """SQLite persistence for projects, messages, and requirements."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        self._init_schema()

    def _migrate(self):
        """Lightweight additive migrations for older databases.

        SQLite's `CREATE TABLE IF NOT EXISTS` won't add columns to an
        existing table, so we ALTER here. Wrapped in try/except since
        these are idempotent — re-running is safe on fresh DBs.
        """
        with sqlite3.connect(self.db_path) as conn:
            existing = {row[1] for row in conn.execute('PRAGMA table_info(messages)').fetchall()}
            if 'project_id' not in existing:
                try:
                    conn.execute('ALTER TABLE messages ADD COLUMN project_id TEXT')
                except sqlite3.OperationalError:
                    pass
            loop_cols = {row[1] for row in conn.execute('PRAGMA table_info(loop_rounds)').fetchall()}
            if 'insert_order' not in loop_cols:
                try:
                    conn.execute('ALTER TABLE loop_rounds ADD COLUMN insert_order INTEGER')
                except sqlite3.OperationalError:
                    pass

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript('\n                CREATE TABLE IF NOT EXISTS projects (\n                    id TEXT PRIMARY KEY,\n                    name TEXT NOT NULL,\n                    description TEXT,\n                    workspace TEXT,\n                    work_dir TEXT,\n                    requirements TEXT,\n                    status TEXT,\n                    created_at REAL,\n                    updated_at REAL\n                );\n\n                CREATE TABLE IF NOT EXISTS messages (\n                    id TEXT PRIMARY KEY,\n                    sender TEXT,\n                    receiver TEXT,\n                    topic TEXT,\n                    content TEXT,\n                    msg_type TEXT,\n                    timestamp REAL,\n                    metadata TEXT,\n                    project_id TEXT\n                );\n\n                -- Structured per-round digest of a LoopReview loop. We keep\n                -- these separately from the raw messages table because\n                -- they\'re what we inject back into the next loop\'s prompt\n                -- (the digest is what the Coder/Reviewer care about; raw\n                -- tool traces are noise).\n                --\n                -- The primary key is (project_id, session_id, round) so\n                -- re-saving the same round is idempotent (INSERT OR REPLACE\n                -- works as intended, instead of appending duplicate rows).\n                CREATE TABLE IF NOT EXISTS loop_rounds (\n                    project_id TEXT NOT NULL,\n                    session_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    coder_summary TEXT,\n                    review_summary TEXT,\n                    review_json TEXT,\n                    score INTEGER,\n                    approve INTEGER,\n                    created_at REAL,\n                    insert_order INTEGER,\n                    PRIMARY KEY (project_id, session_id, round)\n                );\n\n                -- Reference files uploaded to a project (PDF, markdown,\n                -- datasheet, etc.). The full content is stored inline so\n                -- the Coder can see it without touching the filesystem;\n                -- the orchestrator injects a digest of these into the\n                -- loop\'s starting prompt. We don\'t index `content` (FTS5\n                -- could be added later) — list/load is by project_id.\n                CREATE TABLE IF NOT EXISTS project_files (\n                    id TEXT PRIMARY KEY,\n                    project_id TEXT NOT NULL,\n                    name TEXT NOT NULL,\n                    mime TEXT,\n                    size INTEGER,\n                    content TEXT,\n                    uploaded_at REAL\n                );\n\n                -- Inline review comments (one per round, JSON blob).\n                -- UI / editor plugins fetch this and render ::code-comment\n                -- directives. Capped at last 200 rounds per project to\n                -- keep the table small.\n                CREATE TABLE IF NOT EXISTS review_comments (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    comments_json TEXT NOT NULL,\n                    created_at REAL,\n                    UNIQUE(project_id, round)\n                );\n\n                -- Reviewer ask-human history. Each row is one question\n                -- + (optional) user answer. Lets the UI show "the\n                -- reviewer asked 3 questions during this loop".\n                CREATE TABLE IF NOT EXISTS ask_history (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    question TEXT NOT NULL,\n                    context TEXT,\n                    answer TEXT,\n                    asked_at REAL,\n                    answered_at REAL\n                );\n\n                -- Loop checkpoints: durable record of every auto-commit\n                -- the loop made. Mirrors git but queryable without\n                -- spawning a subprocess. Useful for the rollback UI\n                -- when the workspace has been wiped.\n                -- Per-project style/rule preferences. Each rule is a\n                -- short freeform text the user wants the Coder to always\n                -- follow ("use type hints", "no print debugging"). The\n                -- orchestrator injects the active rules into the Coder\n                -- system prompt on the next round. We store as one row\n                -- per rule so a future UI can edit/delete them.\n                CREATE TABLE IF NOT EXISTS project_preferences (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    kind TEXT NOT NULL,\n                    rule TEXT NOT NULL,\n                    created_at REAL\n                );\n\n                CREATE TABLE IF NOT EXISTS loop_checkpoints (\n                    project_id TEXT NOT NULL,\n                    session_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    sha TEXT NOT NULL,\n                    score INTEGER,\n                    approved INTEGER,\n                    summary TEXT,\n                    created_at REAL,\n                    PRIMARY KEY (project_id, session_id, round)\n                );\n\n                -- Distilled project notes. Written by the post-loop\n                -- consolidator (Coder self-reflection) or by the user.\n                -- Each note is a short paragraph describing an\n                -- architecture decision, a convention, or a known pitfall.\n                -- Notes are injected into the next loop as a\n                -- "Project Notes" block before the requirement so the\n                -- Coder can build on prior learnings.\n                CREATE TABLE IF NOT EXISTS project_notes (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    kind TEXT NOT NULL DEFAULT \'convention\',\n                    title TEXT NOT NULL,\n                    body TEXT NOT NULL,\n                    source TEXT NOT NULL DEFAULT \'user\',\n                    use_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL,\n                    updated_at REAL NOT NULL\n                );\n\n                -- Reusable playbook entries. A skill has trigger\n                -- keywords and a body that gets injected into the\n                -- Coder prompt when any trigger matches the current\n                -- requirement or the most recent failure. Skills can\n                -- be user-authored or auto-extracted from successful\n                -- rounds. `confidence` starts at 0.5 and climbs each\n                -- time the skill is referenced in a successful round.\n                CREATE TABLE IF NOT EXISTS project_skills (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    name TEXT NOT NULL,\n                    triggers TEXT NOT NULL DEFAULT \'[]\',\n                    body TEXT NOT NULL,\n                    confidence REAL NOT NULL DEFAULT 0.5,\n                    use_count INTEGER NOT NULL DEFAULT 0,\n                    success_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL,\n                    updated_at REAL NOT NULL\n                );\n\n                -- Working-fix patterns captured from successful rounds.\n                -- Given a from_signature and the fix that actually\n                -- worked, we can match it against future rounds and\n                -- inject the fix into the prompt before the Coder\n                -- tries to rediscover it.\n                CREATE TABLE IF NOT EXISTS working_fixes (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    from_signature TEXT NOT NULL,\n                    fix_body TEXT NOT NULL,\n                    issue_category TEXT NOT NULL DEFAULT \'general\',\n                    success_count INTEGER NOT NULL DEFAULT 1,\n                    failure_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL,\n                    updated_at REAL NOT NULL\n                );\n\n                -- Full-text search over loop_rounds for relevance-\n                -- scored memory retrieval. Uses FTS5 with porter\n                -- stemming; INSERT OR IGNORE so manual edits to\n                -- loop_rounds do not blow up the index.\n                CREATE VIRTUAL TABLE IF NOT EXISTS loop_rounds_fts USING fts5(\n                    project_id, session_id, round UNINDEXED,\n                    coder_summary, review_summary, issues_text,\n                    tokenize = \'porter unicode61\'\n                );\n\n                -- Global cross-project knowledge base. Keyed by\n                -- project_id = empty string so it is queryable as\n                -- "all insights ever seen". Each row is a single\n                -- distilled lesson with category, source project,\n                -- and a use_count so rarely-useful insights bubble up.\n                CREATE TABLE IF NOT EXISTS global_kb (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL DEFAULT \'\',\n                    source_project_id TEXT NOT NULL DEFAULT \'\',\n                    category TEXT NOT NULL DEFAULT \'general\',\n                    body TEXT NOT NULL,\n                    use_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL\n                );\n            ')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_msg_project ON messages(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_loop_project_session ON loop_rounds(project_id, session_id, round)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_comments_project ON review_comments(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_ask_project ON ask_history(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_checkpoints_project ON loop_checkpoints(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_prefs_project ON project_preferences(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_files_project ON project_files(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_notes_project ON project_notes(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_skills_project ON project_skills(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_fixes_project ON working_fixes(project_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_kb_project ON global_kb(project_id)')

    def add_preference(self, project_id: str, kind: str, rule: str) -> int:
        """Insert a style/rule preference for a project.

        `kind` is "always" / "never" / "prefer" - used by the UI to group
        rules and by the prompt builder to phrase the rule.
        Returns the new row id.
        """
        if kind not in ('always', 'never', 'prefer'):
            kind = 'always'
        rule = rule.strip()
        if not rule:
            raise ValueError('rule cannot be empty')
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('INSERT INTO project_preferences (project_id, kind, rule, created_at) VALUES (?, ?, ?, ?)', (project_id, kind, rule, time.time()))
            return cur.lastrowid

    def delete_preference(self, project_id: str, pref_id: int) -> bool:
        """Delete a preference by id (only if it belongs to the project)."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('DELETE FROM project_preferences WHERE id = ? AND project_id = ?', (pref_id, project_id))
            return cur.rowcount > 0

    def list_preferences(self, project_id: str) -> list:
        """List all preferences for a project, oldest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM project_preferences WHERE project_id = ? ORDER BY created_at ASC, id ASC', (project_id,)).fetchall()
        return [dict(r) for r in rows]

    def save_project(self, project):
        """Save or update a project."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('\n                INSERT OR REPLACE INTO projects\n                (id, name, description, workspace, work_dir, requirements, status, created_at, updated_at)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n            ', (project.id, project.name, project.description, str(project.workspace), project.work_dir, project.requirements, project.status, project.created_at, time.time()))

    def load_projects(self) -> List[dict]:
        """Load all projects."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM projects ORDER BY created_at DESC').fetchall()
            return [dict(row) for row in rows]

    def delete_project(self, project_id: str):
        """Delete a project."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM projects WHERE id = ?', (project_id,))
            conn.execute('DELETE FROM project_files WHERE project_id = ?', (project_id,))

    def add_file(self, file_id: str, project_id: str, name: str, mime: str, size: int, content: str) -> None:
        """Insert one reference file. `content` may be text, base64 of
        binary, or any string — we don't try to be clever here."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('\n                INSERT OR REPLACE INTO project_files\n                (id, project_id, name, mime, size, content, uploaded_at)\n                VALUES (?, ?, ?, ?, ?, ?, ?)\n            ', (file_id, project_id, name, mime or 'application/octet-stream', int(size or 0), content or '', time.time()))

    def list_files(self, project_id: str) -> List[dict]:
        """Return all reference files for a project, newest first.

        Note: `content` is loaded lazily in `load_file()` so the list
        endpoint stays light. The list view returns metadata only; the
        Coder's prompt digest is built by `orchestrator.get_reference_digest`.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('\n                SELECT id, project_id, name, mime, size, uploaded_at\n                FROM project_files WHERE project_id = ?\n                ORDER BY uploaded_at DESC\n            ', (project_id,)).fetchall()
            return [dict(r) for r in rows]

    def load_file(self, file_id: str) -> Optional[dict]:
        """Return one reference file (including its content) by id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM project_files WHERE id = ?', (file_id,)).fetchone()
            return dict(row) if row else None

    def get_file(self, file_id: str) -> Optional[dict]:
        """Alias for load_file — some call sites ask for `get_file`.

        Both names refer to the same underlying SQLite row.
        """
        return self.load_file(file_id)

    def delete_file(self, file_id: str) -> bool:
        """Delete one reference file. Returns True if a row was removed."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('DELETE FROM project_files WHERE id = ?', (file_id,))
            return cur.rowcount > 0

    def load_all_files_for_project(self, project_id: str) -> List[dict]:
        """Return ALL reference files for a project, including content.
        Used by the orchestrator to build the Coder's prompt digest."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('\n                SELECT id, name, mime, size, content\n                FROM project_files WHERE project_id = ?\n                ORDER BY uploaded_at ASC\n            ', (project_id,)).fetchall()
            return [dict(r) for r in rows]

    def save_message(self, msg):
        """Save a message."""
        project_id = (msg.metadata or {}).get('project_id', '')
        if not project_id and msg.sender.startswith('user') is False:
            if '.' in msg.sender:
                project_id = msg.sender.split('.', 1)[0]
        if not project_id and msg.receiver and ('.' in msg.receiver):
            project_id = msg.receiver.split('.', 1)[0]
        with sqlite3.connect(self.db_path) as conn:
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            metadata = json.dumps(msg.metadata, ensure_ascii=False) if msg.metadata else '{}'
            conn.execute('\n                INSERT OR REPLACE INTO messages\n                (id, sender, receiver, topic, content, msg_type, timestamp, metadata, project_id)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n            ', (msg.id, msg.sender, msg.receiver, msg.topic, content, msg.msg_type, msg.timestamp, metadata, project_id))

    def load_messages(self, limit: int=100, project_id: Optional[str]=None) -> List[dict]:
        """Load recent messages, optionally scoped to a single project."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if project_id is None:
                rows = conn.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?', (limit,)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM messages WHERE project_id = ? ORDER BY timestamp DESC LIMIT ?', (project_id, limit)).fetchall()
            return [dict(row) for row in rows]

    def save_loop_round(self, project_id: str, session_id: str, round_no: int, coder_summary: str, review: dict) -> None:
        """Persist one round's structured digest. Idempotent on
        (project_id, session_id, round) — if you save twice, the row is
        overwritten (the table's PRIMARY KEY makes INSERT OR REPLACE work)."""
        review_json = json.dumps(review, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            insert_order = self._next_loop_insert_order(conn)
            conn.execute('INSERT OR REPLACE INTO loop_rounds (project_id, session_id, round, coder_summary, review_summary, review_json, score, approve, created_at, insert_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (project_id, session_id, round_no, coder_summary[:5000], (review.get('summary') or '')[:2000], review_json, int(review.get('score') or 0), 1 if review.get('approve') else 0, time.time(), insert_order))

    def _next_loop_insert_order(self, conn) -> int:
        """Return a monotonically-increasing integer used as the
        wall-clock tiebreaker when sorting rounds. We compute it from
        MAX(insert_order)+1 (or 1 if empty) so each connection observes
        the same sequence even when multiple processes write at once.
        """
        try:
            row = conn.execute('SELECT COALESCE(MAX(insert_order), 0) FROM loop_rounds').fetchone()
            return int(row[0] or 0) + 1
        except sqlite3.OperationalError:
            return int(time.time() * 1000)

    def load_loop_rounds(self, project_id: str, limit: int=20) -> List[dict]:
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
            rows = conn.execute('SELECT * FROM loop_rounds WHERE project_id = ?', (project_id,)).fetchall()

        def _sort_key(r):
            return (r.get('created_at') or 0, r.get('insert_order') or 0)
        ordered = sorted([dict(r) for r in rows], key=_sort_key)
        return ordered[:limit]

    def load_last_loop_summary(self, project_id: str) -> Optional[str]:
        """One-line digest of the most recent loop's last round — useful
        for the UI to show "last time this project..." without rehydrating
        full history."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('\n                SELECT round, score, approve, review_summary\n                FROM loop_rounds\n                WHERE project_id = ?\n                ORDER BY created_at DESC, round DESC\n                LIMIT 1\n            ', (project_id,)).fetchone()
        if not row:
            return None
        round_no, score, approve, summary = row
        verdict = 'approved' if approve else 'rejected'
        return f'R{round_no} {verdict} (score {score}): {summary[:150]}'

    def delete_loop_rounds(self, project_id: str) -> None:
        """When a project is deleted, clean up its loop memory."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM loop_rounds WHERE project_id = ?', (project_id,))
            try:
                conn.execute('DELETE FROM loop_rounds_fts WHERE project_id = ?', (project_id,))
            except sqlite3.OperationalError:
                pass

    def add_project_note(self, project_id: str, kind: str, title: str, body: str, source: str='user') -> int:
        """Persist a single project note. Returns the row id."""
        kind = (kind or 'convention').strip().lower()
        if kind not in ('convention', 'pitfall', 'architecture', 'fact'):
            kind = 'convention'
        title = (title or '').strip()[:120]
        body = (body or '').strip()[:2000]
        if not body:
            raise ValueError('note body cannot be empty')
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('INSERT INTO project_notes (project_id, kind, title, body, source, use_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)', (project_id, kind, title, body, source or 'user', now, now))
            return cur.lastrowid or 0

    def list_project_notes(self, project_id: str, limit: int=20) -> List[dict]:
        """Most-used notes first; ties broken by recency. Bounded
        so the Coder prompt does not balloon."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM project_notes WHERE project_id = ?' + ' ORDER BY use_count DESC, updated_at DESC LIMIT ?', (project_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def delete_project_note(self, project_id: str, note_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('DELETE FROM project_notes WHERE id = ? AND project_id = ?', (note_id, project_id))
            return cur.rowcount > 0

    def bump_project_note_use(self, note_id: int) -> None:
        """Increment use_count when a note is injected into a prompt."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE project_notes SET use_count = use_count + 1,' + ' updated_at = ? WHERE id = ?', (time.time(), note_id))

    def add_skill(self, project_id: str, name: str, triggers: list, body: str, confidence: float=0.5, source: str='user') -> int:
        """Insert a skill. `triggers` is a list of keyword strings;
        the body is injected when any keyword appears in the prompt.
        `source` is one of "user" / "consolidator" / "imported".
        Returns the new row id."""
        name = (name or '').strip()[:120]
        body = (body or '').strip()[:2000]
        if not name or not body:
            raise ValueError('skill name and body are required')
        triggers_json = json.dumps(list(triggers or []), ensure_ascii=False)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('INSERT INTO project_skills (project_id, name, triggers, body, confidence, use_count, success_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)', (project_id, name, triggers_json, body, confidence, now, now))
            return cur.lastrowid or 0

    def find_skills_for(self, project_id: str, text: str, limit: int=5) -> List[dict]:
        """Return up to `limit` skills whose trigger keywords
        appear in `text` (case-insensitive substring match).
        Sorted by confidence DESC then use_count DESC."""
        if not text:
            return []
        text_lower = text.lower()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM project_skills WHERE project_id = ?', (project_id,)).fetchall()
        matches = []
        for r in rows:
            triggers_raw = r['triggers'] or '[]'
            try:
                triggers = json.loads(triggers_raw)
            except (TypeError, ValueError):
                triggers = []
            for trig in triggers:
                if trig and str(trig).lower() in text_lower:
                    matches.append(dict(r))
                    break
        matches.sort(key=lambda d: (-float(d.get('confidence') or 0.5), -(d.get('use_count') or 0)))
        return matches[:limit]

    def list_skills(self, project_id: str) -> List[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM project_skills WHERE project_id = ?' + ' ORDER BY confidence DESC, use_count DESC', (project_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_skill(self, project_id: str, skill_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('DELETE FROM project_skills WHERE id = ? AND project_id = ?', (skill_id, project_id))
            return cur.rowcount > 0

    def record_skill_outcome(self, skill_id: int, success: bool) -> None:
        """Called after a loop round where the skill was injected.
        success=True climbs confidence (capped at 1.0); False lowers
        it. Also bumps use_count unconditionally."""
        delta = 0.05 if success else -0.1
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE project_skills SET use_count = use_count + 1, success_count = success_count + ?, confidence = MAX(0.0, MIN(1.0, confidence + ?)), updated_at = ? WHERE id = ?', (1 if success else 0, delta, time.time(), skill_id))

    def add_working_fix(self, project_id: str, from_signature: str, fix_body: str, issue_category: str='general') -> int:
        """Insert a working-fix pattern. If a row with the same
        (project_id, from_signature) exists, increment success_count
        instead of duplicating."""
        fix_body = (fix_body or '').strip()[:2000]
        from_signature = (from_signature or '').strip()[:500]
        if not fix_body or not from_signature:
            raise ValueError('from_signature and fix_body required')
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute('SELECT id FROM working_fixes' + ' WHERE project_id = ? AND from_signature = ?', (project_id, from_signature)).fetchone()
            if existing:
                existing_id = existing[0] if not isinstance(existing, dict) else existing['id']
                conn.execute('UPDATE working_fixes SET success_count = success_count + 1, fix_body = ?, issue_category = ?, updated_at = ? WHERE id = ?', (fix_body, issue_category, now, existing_id))
                return existing_id
            cur = conn.execute('INSERT INTO working_fixes (project_id, from_signature, fix_body, issue_category, success_count, failure_count, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 0, ?, ?)', (project_id, from_signature, fix_body, issue_category, now, now))
            return cur.lastrowid or 0

    def find_working_fix(self, project_id: str, signature: str) -> Optional[dict]:
        """Exact-signature lookup. Returns the highest-success
        matching row or None."""
        if not signature:
            return None
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM working_fixes WHERE project_id = ? AND from_signature = ? ORDER BY success_count DESC LIMIT 1', (project_id, signature)).fetchone()
        return dict(row) if row else None

    def record_fix_outcome(self, fix_id: int, success: bool) -> None:
        with sqlite3.connect(self.db_path) as conn:
            if success:
                conn.execute('UPDATE working_fixes SET success_count = success_count + 1,' + ' updated_at = ? WHERE id = ?', (time.time(), fix_id))
            else:
                conn.execute('UPDATE working_fixes SET failure_count = failure_count + 1,' + ' updated_at = ? WHERE id = ?', (time.time(), fix_id))

    def index_loop_round(self, project_id: str, session_id: str, round_no: int, coder_summary: str, review_summary: str, issues_text: str) -> None:
        """Mirror a round into the FTS table. Errors are swallowed
        so a corrupt FTS index never breaks the loop."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('INSERT OR REPLACE INTO loop_rounds_fts (project_id, session_id, round, coder_summary, review_summary, issues_text) VALUES (?, ?, ?, ?, ?, ?)', (project_id, session_id, round_no, coder_summary or '', review_summary or '', issues_text or ''))
        except sqlite3.OperationalError:
            logger.debug('FTS index write failed', exc_info=True)

    def search_loop_rounds(self, project_id: str, query: str, limit: int=5) -> List[dict]:
        """BM25-ranked full-text search across the project is
        loop history. Returns rows with the original `round`,
        `coder_summary`, `review_summary`, and a `score` column
        (lower = more relevant; SQLite bm25 returns negative).
        Empty query falls back to the most recent N rounds."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if not query:
                rows = conn.execute('SELECT round, coder_summary, review_summary FROM loop_rounds WHERE project_id = ? ORDER BY round DESC LIMIT ?', (project_id, limit)).fetchall()
                if rows:
                    return [dict(r) for r in rows]
                # Nothing in loop_rounds (only FTS was indexed) — fall back.
                rows = conn.execute('SELECT round, coder_summary, review_summary FROM loop_rounds_fts WHERE project_id = ? ORDER BY round DESC LIMIT ?', (project_id, limit)).fetchall()
                return [dict(r) for r in rows]
            try:
                rows = conn.execute('SELECT lr.round, lr.coder_summary, lr.review_summary, fts.rank AS score FROM loop_rounds_fts fts JOIN loop_rounds lr ON     lr.project_id = fts.project_id     AND lr.session_id = fts.session_id     AND lr.round = fts.round WHERE fts.project_id = ? AND loop_rounds_fts MATCH ? ORDER BY fts.rank LIMIT ?', (project_id, query, limit)).fetchall()
                if rows:
                    return [dict(r) for r in rows]
                # JOIN found nothing in loop_rounds (caller only indexed
                # into FTS without saving the canonical round). Fall back
                # to FTS-only so the search still returns useful hits.
                rows = conn.execute('SELECT round, coder_summary, review_summary FROM loop_rounds_fts WHERE project_id = ? AND loop_rounds_fts MATCH ? ORDER BY rank LIMIT ?', (project_id, query, limit)).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                logger.debug('FTS query failed for %r', query, exc_info=True)
                rows = conn.execute('SELECT round, coder_summary, review_summary FROM loop_rounds WHERE project_id = ? ORDER BY round DESC LIMIT ?', (project_id, limit)).fetchall()
                return [dict(r) for r in rows]

    def add_global_insight(self, category: str, body: str, source_project_id: str='', min_use_threshold: int=0) -> int:
        """Add an insight to the global KB. We de-duplicate on
        (category, body) substring: if an existing insight already
        covers this lesson, we bump its use_count instead of
        inserting a duplicate."""
        body = (body or '').strip()[:500]
        if not body:
            raise ValueError('insight body required')
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute('SELECT id, use_count FROM global_kb WHERE project_id = ? AND category = ? AND body LIKE ?', ('', category, body[:80])).fetchone()
            if existing:
                existing_id = existing[0] if not isinstance(existing, dict) else existing['id']
                conn.execute('UPDATE global_kb SET use_count = use_count + 1' + ' WHERE id = ?', (existing_id,))
                return existing_id
            cur = conn.execute('INSERT INTO global_kb (project_id, source_project_id, category, body, use_count, created_at) VALUES (?, ?, ?, ?, ?, ?)', ('', source_project_id or '', category, body, max(1, min_use_threshold), time.time()))
            return cur.lastrowid or 0

    def search_global_insights(self, query: str, limit: int=5) -> List[dict]:
        """Pull insights whose body matches the query terms. We
        fall back to a LIKE query when FTS is unavailable so this
        still works on stripped-down DBs."""
        if not query:
            return []
        terms = [t for t in query.lower().split() if len(t) >= 3][:5]
        if not terms:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            like_clauses = ' OR '.join(['body LIKE ?'] * len(terms))
            params = [f'%{t}%' for t in terms]
            rows = conn.execute(f'SELECT * FROM global_kb WHERE project_id = ? AND ({like_clauses})' + ' ORDER BY use_count DESC, created_at DESC LIMIT ?', ('', *params, limit)).fetchall()
        return [dict(r) for r in rows]

    def bump_global_insight_use(self, insight_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE global_kb SET use_count = use_count + 1 WHERE id = ?', (insight_id,))

    def save_review_comments(self, project_id: str, round_no: int, comments: list) -> None:
        """Persist a round's inline review comments. Idempotent on
        (project_id, round) — INSERT OR REPLACE makes re-saving safe
        (the reviewer may emit a slightly different verdict on retry).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('\n                INSERT OR REPLACE INTO review_comments\n                (project_id, round, comments_json, created_at)\n                VALUES (?, ?, ?, ?)\n            ', (project_id, round_no, json.dumps(comments, ensure_ascii=False), time.time()))

    def load_review_comments(self, project_id: str, round_no: int=0) -> list:
        """Load comments for a specific round (round=0 = latest).

        Returns [] if nothing stored yet.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if round_no == 0:
                row = conn.execute('\n                    SELECT comments_json FROM review_comments\n                    WHERE project_id = ?\n                    ORDER BY round DESC LIMIT 1\n                ', (project_id,)).fetchone()
            else:
                row = conn.execute('\n                    SELECT comments_json FROM review_comments\n                    WHERE project_id = ? AND round = ?\n                ', (project_id, round_no)).fetchone()
        if not row:
            return []
        try:
            return json.loads(row['comments_json'])
        except (json.JSONDecodeError, TypeError):
            return []

    def record_ask(self, project_id: str, round_no: int, question: str, context: str='') -> int:
        """Persist a Reviewer ask_human. Returns the ask row id so the
        caller can later match it to an answer."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('\n                INSERT INTO ask_history\n                (project_id, round, question, context, asked_at)\n                VALUES (?, ?, ?, ?, ?)\n            ', (project_id, round_no, question, context[:1000], time.time()))
            return cur.lastrowid or 0

    def record_ask_answer(self, ask_id: int, answer: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('\n                UPDATE ask_history SET answer = ?, answered_at = ?\n                WHERE id = ?\n            ', (answer, time.time(), ask_id))

    def load_ask_history(self, project_id: str, limit: int=50) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('\n                SELECT * FROM ask_history\n                WHERE project_id = ?\n                ORDER BY asked_at DESC LIMIT ?\n            ', (project_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def save_checkpoint(self, project_id: str, session_id: str, round_no: int, sha: str, score: int, approved: bool, summary: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('\n                INSERT OR REPLACE INTO loop_checkpoints\n                (project_id, session_id, round, sha, score, approved, summary, created_at)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n            ', (project_id, session_id, round_no, sha, score, 1 if approved else 0, summary[:500], time.time()))

    def load_checkpoints(self, project_id: str, limit: int=100) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('\n                SELECT round, sha, score, approved, summary, created_at\n                FROM loop_checkpoints\n                WHERE project_id = ?\n                ORDER BY round DESC LIMIT ?\n            ', (project_id, limit)).fetchall()
        return [{'round': r['round'], 'sha': r['sha'], 'score': r['score'] or 0, 'approved': bool(r['approved']), 'summary': r['summary'] or '', 'ts': r['created_at'] or 0} for r in rows]

    def delete_project_memory(self, project_id: str) -> None:
        """Wipe every per-project table when the project is deleted."""
        with sqlite3.connect(self.db_path) as conn:
            for tbl in ('messages', 'loop_rounds', 'project_files', 'review_comments', 'ask_history', 'loop_checkpoints', 'project_preferences', 'project_notes', 'project_skills', 'working_fixes'):
                conn.execute(f'DELETE FROM {tbl} WHERE project_id = ?', (project_id,))
            try:
                conn.execute('DELETE FROM loop_rounds_fts WHERE project_id = ?', (project_id,))
            except sqlite3.OperationalError:
                pass
