"""Persistence mixin: KnowledgeStoreMixin, split out of kairos.core.persistence."""
from __future__ import annotations
import json
import sqlite3
import time
from typing import List, Optional


class KnowledgeStoreMixin:
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

    def list_working_fixes(self, project_id: str, limit: int = 5) -> List[dict]:
        """Return the most recent N working-fix rows for a project.

        The previous implementation selected ``issue`` / ``fix`` /
        ``severity`` — columns that do not exist on ``working_fixes``
        (the live schema is ``from_signature`` / ``fix_body`` /
        ``issue_category``) — so every call raised and was swallowed by
        the caller. It also had a sibling ``record_working_fix`` that
        INSERTed the same fictitious columns; both had zero callers. The
        canonical writer is ``add_working_fix``.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT from_signature, fix_body, issue_category, created_at "
                "FROM working_fixes WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit)).fetchall()
        return [dict(r) for r in rows]

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
