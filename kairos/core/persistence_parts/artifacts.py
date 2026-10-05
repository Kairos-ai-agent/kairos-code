"""Persistence mixin: ArtifactStoreMixin, split out of kairos.core.persistence."""
from __future__ import annotations
import json
import sqlite3
import time
from typing import List, Optional


class ArtifactStoreMixin:
    def add_artifact(self, artifact: dict) -> str:
        """Store one produced thing. Idempotent on `id`."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT OR REPLACE INTO artifacts '
                '(id, project_id, session_id, round_no, kind, title, body, path, '
                ' created_at, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (artifact['id'], artifact['project_id'],
                 artifact.get('session_id') or '',
                 int(artifact.get('round_no') or 0),
                 artifact['kind'], artifact['title'],
                 artifact.get('body') or '', artifact.get('path') or '',
                 float(artifact.get('created_at') or time.time()),
                 json.dumps(artifact.get('meta') or {}, ensure_ascii=False)))
        return artifact['id']

    def list_artifacts(self, project_id: str, kind: str = '',
                       limit: int = 50) -> List[dict]:
        """Newest first: the thing you just produced is the thing you want.

        `meta` is stored as JSON text and comes back as a dict, so callers never
        have to know that.
        """
        query = 'SELECT * FROM artifacts WHERE project_id = ?'
        params = [project_id]
        if kind:
            query += ' AND kind = ?'
            params.append(kind)
        query += ' ORDER BY created_at DESC, id DESC LIMIT ?'
        params.append(max(1, int(limit)))

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._artifact_row(dict(r)) for r in rows]

    def get_artifact(self, artifact_id: str) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM artifacts WHERE id = ?',
                               (artifact_id,)).fetchone()
        return self._artifact_row(dict(row)) if row else None

    def delete_artifact(self, artifact_id: str) -> bool:
        """Delete an artifact and its thread. Comments outlive nothing."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('DELETE FROM artifacts WHERE id = ?',
                                  (artifact_id,))
            conn.execute('DELETE FROM artifact_comments WHERE artifact_id = ?',
                         (artifact_id,))
        return bool(cursor.rowcount)

    def add_artifact_comment(self, artifact_id: str, author: str,
                             body: str) -> dict:
        created = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'INSERT INTO artifact_comments (artifact_id, author, body, '
                'created_at) VALUES (?, ?, ?, ?)',
                (artifact_id, author or 'user', body, created))
        return {'id': cursor.lastrowid, 'artifact_id': artifact_id,
                'author': author or 'user', 'body': body,
                'created_at': created}

    def list_artifact_comments(self, artifact_id: str,
                               limit: int = 100) -> List[dict]:
        """Oldest first: a thread reads top to bottom."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT * FROM artifact_comments WHERE artifact_id = ? '
                'ORDER BY created_at ASC, id ASC LIMIT ?',
                (artifact_id, max(1, int(limit)))).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _artifact_row(row: dict) -> dict:
        """Decode the one column that is stored as text."""
        try:
            row['meta'] = json.loads(row.get('meta') or '{}')
        except (json.JSONDecodeError, TypeError):
            row['meta'] = {}
        return row
