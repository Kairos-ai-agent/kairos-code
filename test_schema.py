import sqlite3
import os
import tempfile

# Just test the SQL statements directly
db_path = tempfile.NamedTemporaryFile(suffix='.db', delete=False).name

schema_sql = """
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

CREATE TABLE IF NOT EXISTS project_files (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT,
    size INTEGER,
    content TEXT,
    uploaded_at REAL
);

CREATE TABLE IF NOT EXISTS review_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    comments_json TEXT NOT NULL,
    created_at REAL,
    UNIQUE(project_id, round)
);

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

CREATE TABLE IF NOT EXISTS project_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    rule TEXT NOT NULL,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS project_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'convention',
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS project_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    triggers TEXT NOT NULL DEFAULT '[]',
    body TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    use_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS working_fixes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    from_signature TEXT NOT NULL,
    fix_body TEXT NOT NULL,
    issue_category TEXT NOT NULL DEFAULT 'general',
    success_count INTEGER NOT NULL DEFAULT 1,
    failure_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS loop_rounds_fts USING fts5(
    project_id, session_id, round UNINDEXED,
    coder_summary, review_summary, issues_text,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS global_kb (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL DEFAULT '',
    source_project_id TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    body TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""

with sqlite3.connect(db_path) as conn:
    conn.executescript(schema_sql)
    conn.execute("INSERT INTO projects VALUES ('p1', 'T', 'D', 'ws', 'wd', 'req', 'active', 0, 0)")
    conn.execute("INSERT INTO project_notes (project_id, kind, title, body, created_at, updated_at) VALUES ('p1', 'convention', 'use type hints', 'Always annotate function signatures', 0, 0)")
    conn.execute("INSERT INTO project_skills (project_id, name, triggers, body, created_at, updated_at) VALUES ('p1', 'pytest', '[\"test\", \"pytest\"]', 'Run pytest -q before declaring done', 0, 0)")
    conn.execute("INSERT INTO working_fixes (project_id, from_signature, fix_body, created_at, updated_at) VALUES ('p1', 'auth/login.py:sql-injection', 'Use parameterized queries', 0, 0)")
    conn.execute("INSERT INTO loop_rounds (project_id, session_id, round, coder_summary, review_summary, review_json, score, approve, created_at, insert_order) VALUES ('p1', 's1', 1, 'implemented login', 'looks good', '{}', 80, 1, 100, 1)")
    conn.execute("INSERT INTO loop_rounds_fts (project_id, session_id, round, coder_summary, review_summary, issues_text) VALUES ('p1', 's1', 1, 'implemented login page', 'reviewer accepted', 'minor nits')")
    conn.execute("INSERT INTO global_kb (project_id, source_project_id, category, body, use_count, created_at) VALUES ('', 'p1', 'style', 'prefer f-strings over .format()', 1, 0)")

with sqlite3.connect(db_path) as conn:
    conn.row_factory = sqlite3.Row
    n_notes = conn.execute('SELECT COUNT(*) AS c FROM project_notes').fetchone()['c']
    n_skills = conn.execute('SELECT COUNT(*) AS c FROM project_skills').fetchone()['c']
    n_fixes = conn.execute('SELECT COUNT(*) AS c FROM working_fixes').fetchone()['c']
    n_fts = conn.execute('SELECT COUNT(*) AS c FROM loop_rounds_fts').fetchone()['c']
    n_kb = conn.execute('SELECT COUNT(*) AS c FROM global_kb').fetchone()['c']
    print(f'notes={n_notes} skills={n_skills} fixes={n_fixes} fts={n_fts} kb={n_kb}')
    
    # Test FTS5 search
    rows = conn.execute(
        "SELECT round FROM loop_rounds_fts WHERE loop_rounds_fts MATCH 'login'"
    ).fetchall()
    print(f'FTS hits for "login": {len(rows)}')

os.unlink(db_path)
print('OK')
