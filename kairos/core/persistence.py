"""SQLite persistence for projects, messages, and requirements."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from kairos.core.persistence_parts.projects import ProjectStoreMixin
from kairos.core.persistence_parts.files import FileStoreMixin
from kairos.core.persistence_parts.messages import MessageStoreMixin
from kairos.core.persistence_parts.rounds import RoundStoreMixin
from kairos.core.persistence_parts.knowledge import KnowledgeStoreMixin
from kairos.core.persistence_parts.reviews import ReviewStoreMixin
from kairos.core.persistence_parts.artifacts import ArtifactStoreMixin


class Persistence(ProjectStoreMixin, FileStoreMixin, MessageStoreMixin, RoundStoreMixin, KnowledgeStoreMixin, ReviewStoreMixin, ArtifactStoreMixin):
    """SQLite persistence for projects, messages, and requirements."""

    CHAT_TOPICS = ('user.chat', 'agent.chat', 'agent.chat_reply',
                   'agent.message', 'agent.response',
                   # R38.8: the agent's *process* — a turn starting, a tool
                   # being called, what it returned, an error — belongs in the
                   # thread too. It already rendered live over the WebSocket,
                   # but it was not in this whitelist, so the moment the user
                   # refreshed or reopened the session the whole process
                   # vanished and the agent looked like it had done nothing.
                   #
                   # Trade-off: a tool-heavy project now spends part of its
                   # newest-N window on process rows instead of conversation.
                   # That is the point (the user wants to see the process), but
                   # callers that need more *turns* should page back with the
                   # ``before_ts``/``before_id`` cursor or raise ``limit``.
                   'agent.thinking', 'tool.call', 'tool.result', 'task.error')


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
            # R38.6: add ``archived_at`` column for soft-delete. DELETE
            # /api/projects/{id} now just sets archived_at — the
            # project record, sessions, and files are preserved so
            # the user can restore later. ``load_projects`` filters
            # out archived rows by default.
            project_cols = {row[1] for row in
                            conn.execute('PRAGMA table_info(projects)').fetchall()}
            if 'archived_at' not in project_cols:
                try:
                    conn.execute(
                        'ALTER TABLE projects ADD COLUMN archived_at REAL')
                except sqlite3.OperationalError:
                    pass

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript('\n                CREATE TABLE IF NOT EXISTS projects (\n                    id TEXT PRIMARY KEY,\n                    name TEXT NOT NULL,\n                    description TEXT,\n                    workspace TEXT,\n                    work_dir TEXT,\n                    requirements TEXT,\n                    status TEXT,\n                    created_at REAL,\n                    updated_at REAL,\n                    archived_at REAL\n                );\n\n                CREATE TABLE IF NOT EXISTS messages (\n                    id TEXT PRIMARY KEY,\n                    sender TEXT,\n                    receiver TEXT,\n                    topic TEXT,\n                    content TEXT,\n                    msg_type TEXT,\n                    timestamp REAL,\n                    metadata TEXT,\n                    project_id TEXT\n                );\n\n                -- Structured per-round digest of a LoopReview loop. We keep\n                -- these separately from the raw messages table because\n                -- they\'re what we inject back into the next loop\'s prompt\n                -- (the digest is what the Coder/Reviewer care about; raw\n                -- tool traces are noise).\n                --\n                -- The primary key is (project_id, session_id, round) so\n                -- re-saving the same round is idempotent (INSERT OR REPLACE\n                -- works as intended, instead of appending duplicate rows).\n                CREATE TABLE IF NOT EXISTS loop_rounds (\n                    project_id TEXT NOT NULL,\n                    session_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    coder_summary TEXT,\n                    review_summary TEXT,\n                    review_json TEXT,\n                    score INTEGER,\n                    approve INTEGER,\n                    created_at REAL,\n                    insert_order INTEGER,\n                    PRIMARY KEY (project_id, session_id, round)\n                );\n\n                -- Reference files uploaded to a project (PDF, markdown,\n                -- datasheet, etc.). The full content is stored inline so\n                -- the Coder can see it without touching the filesystem;\n                -- the orchestrator injects a digest of these into the\n                -- loop\'s starting prompt. We don\'t index `content` (FTS5\n                -- could be added later) — list/load is by project_id.\n                CREATE TABLE IF NOT EXISTS project_files (\n                    id TEXT PRIMARY KEY,\n                    project_id TEXT NOT NULL,\n                    name TEXT NOT NULL,\n                    mime TEXT,\n                    size INTEGER,\n                    content TEXT,\n                    uploaded_at REAL\n                );\n\n                -- Inline review comments (one per round, JSON blob).\n                -- UI / editor plugins fetch this and render ::code-comment\n                -- directives. Capped at last 200 rounds per project to\n                -- keep the table small.\n                CREATE TABLE IF NOT EXISTS review_comments (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    comments_json TEXT NOT NULL,\n                    created_at REAL,\n                    UNIQUE(project_id, round)\n                );\n\n                -- Reviewer ask-human history. Each row is one question\n                -- + (optional) user answer. Lets the UI show "the\n                -- reviewer asked 3 questions during this loop".\n                CREATE TABLE IF NOT EXISTS ask_history (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    question TEXT NOT NULL,\n                    context TEXT,\n                    answer TEXT,\n                    asked_at REAL,\n                    answered_at REAL\n                );\n\n                -- Loop checkpoints: durable record of every auto-commit\n                -- the loop made. Mirrors git but queryable without\n                -- spawning a subprocess. Useful for the rollback UI\n                -- when the workspace has been wiped.\n                -- Per-project style/rule preferences. Each rule is a\n                -- short freeform text the user wants the Coder to always\n                -- follow ("use type hints", "no print debugging"). The\n                -- orchestrator injects the active rules into the Coder\n                -- system prompt on the next round. We store as one row\n                -- per rule so a future UI can edit/delete them.\n                CREATE TABLE IF NOT EXISTS project_preferences (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    kind TEXT NOT NULL,\n                    rule TEXT NOT NULL,\n                    created_at REAL\n                );\n\n                CREATE TABLE IF NOT EXISTS loop_checkpoints (\n                    project_id TEXT NOT NULL,\n                    session_id TEXT NOT NULL,\n                    round INTEGER NOT NULL,\n                    sha TEXT NOT NULL,\n                    score INTEGER,\n                    approved INTEGER,\n                    summary TEXT,\n                    created_at REAL,\n                    PRIMARY KEY (project_id, session_id, round)\n                );\n\n                -- Distilled project notes. Written by the post-loop\n                -- consolidator (Coder self-reflection) or by the user.\n                -- Each note is a short paragraph describing an\n                -- architecture decision, a convention, or a known pitfall.\n                -- Notes are injected into the next loop as a\n                -- "Project Notes" block before the requirement so the\n                -- Coder can build on prior learnings.\n                CREATE TABLE IF NOT EXISTS project_notes (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    kind TEXT NOT NULL DEFAULT \'convention\',\n                    title TEXT NOT NULL,\n                    body TEXT NOT NULL,\n                    source TEXT NOT NULL DEFAULT \'user\',\n                    use_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL,\n                    updated_at REAL NOT NULL\n                );\n\n                -- Reusable playbook entries. A skill has trigger\n                -- keywords and a body that gets injected into the\n                -- Coder prompt when any trigger matches the current\n                -- requirement or the most recent failure. Skills can\n                -- be user-authored or auto-extracted from successful\n                -- rounds. `confidence` starts at 0.5 and climbs each\n                -- time the skill is referenced in a successful round.\n                CREATE TABLE IF NOT EXISTS project_skills (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    name TEXT NOT NULL,\n                    triggers TEXT NOT NULL DEFAULT \'[]\',\n                    body TEXT NOT NULL,\n                    confidence REAL NOT NULL DEFAULT 0.5,\n                    use_count INTEGER NOT NULL DEFAULT 0,\n                    success_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL,\n                    updated_at REAL NOT NULL\n                );\n\n                -- Working-fix patterns captured from successful rounds.\n                -- Given a from_signature and the fix that actually\n                -- worked, we can match it against future rounds and\n                -- inject the fix into the prompt before the Coder\n                -- tries to rediscover it.\n                CREATE TABLE IF NOT EXISTS working_fixes (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL,\n                    from_signature TEXT NOT NULL,\n                    fix_body TEXT NOT NULL,\n                    issue_category TEXT NOT NULL DEFAULT \'general\',\n                    success_count INTEGER NOT NULL DEFAULT 1,\n                    failure_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL,\n                    updated_at REAL NOT NULL\n                );\n\n                -- Full-text search over loop_rounds for relevance-\n                -- scored memory retrieval. Uses FTS5 with porter\n                -- stemming; INSERT OR IGNORE so manual edits to\n                -- loop_rounds do not blow up the index.\n                CREATE VIRTUAL TABLE IF NOT EXISTS loop_rounds_fts USING fts5(\n                    project_id, session_id, round UNINDEXED,\n                    coder_summary, review_summary, issues_text,\n                    tokenize = \'porter unicode61\'\n                );\n\n                -- Global cross-project knowledge base. Keyed by\n                -- project_id = empty string so it is queryable as\n                -- "all insights ever seen". Each row is a single\n                -- distilled lesson with category, source project,\n                -- and a use_count so rarely-useful insights bubble up.\n                CREATE TABLE IF NOT EXISTS global_kb (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    project_id TEXT NOT NULL DEFAULT \'\',\n                    source_project_id TEXT NOT NULL DEFAULT \'\',\n                    category TEXT NOT NULL DEFAULT \'general\',\n                    body TEXT NOT NULL,\n                    use_count INTEGER NOT NULL DEFAULT 0,\n                    created_at REAL NOT NULL\n                );\n            ')
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

            # Artifacts: what a run produced, kept as a thing with an
            # address so it can be reopened, linked and commented on.
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT DEFAULT '',
                    round_no INTEGER DEFAULT 0,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT DEFAULT '',
                    path TEXT DEFAULT '',
                    created_at REAL DEFAULT 0,
                    meta TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS artifact_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    author TEXT DEFAULT 'user',
                    body TEXT NOT NULL,
                    created_at REAL DEFAULT 0
                );
            """)
            conn.execute('CREATE INDEX IF NOT EXISTS idx_artifacts_project '
                         'ON artifacts(project_id, created_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_artifact_comments '
                         'ON artifact_comments(artifact_id, created_at)')
