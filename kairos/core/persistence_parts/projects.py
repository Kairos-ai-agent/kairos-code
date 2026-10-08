"""Persistence mixin: ProjectStoreMixin, split out of kairos.core.persistence."""
from __future__ import annotations
import sqlite3
import time
from typing import List


class ProjectStoreMixin:
    def save_project(self, project):
        """Save or update a project."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('\n                INSERT OR REPLACE INTO projects\n                (id, name, description, workspace, work_dir, requirements, status, created_at, updated_at)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n            ', (project.id, project.name, project.description, str(project.workspace), project.work_dir, project.requirements, project.status, project.created_at, time.time()))

    def load_projects(self, include_archived: bool = False) -> List[dict]:
        """Load all projects.

        R38.6: by default, archived projects (soft-deleted) are
        filtered out. Pass ``include_archived=True`` to get the
        full list (used by the restore UI / admin views).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if include_archived:
                rows = conn.execute(
                    'SELECT * FROM projects ORDER BY created_at DESC'
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM projects '
                    'WHERE archived_at IS NULL '
                    'ORDER BY created_at DESC'
                ).fetchall()
            return [dict(row) for row in rows]

    def archive_project(self, project_id: str) -> bool:
        """Soft-delete: set ``archived_at`` instead of DELETE.

        The project record, sessions, files, and notes are all
        preserved — only the row is hidden from ``load_projects()``.
        Returns True if a row was archived, False if the project
        didn't exist or was already archived.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'UPDATE projects SET archived_at = ? '
                'WHERE id = ? AND archived_at IS NULL',
                (time.time(), project_id),
            )
            return cur.rowcount > 0

    def restore_project(self, project_id: str) -> bool:
        """Un-archive: clear ``archived_at`` so the project shows up
        in ``load_projects()`` again. Returns True if a row was
        restored."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'UPDATE projects SET archived_at = NULL WHERE id = ?',
                (project_id,),
            )
            return cur.rowcount > 0

    def delete_project(self, project_id: str):
        """Hard-delete a project — removes the row from the DB.

        Most callers should use ``archive_project`` (R38.6 — soft
        delete preserves data for restore). This method is kept for
        the rare case where the user wants to permanently remove a
        project's data (e.g. an admin tool). The frontend no longer
        calls this directly; the chat sidebar's delete button goes
        through ``archive_project`` via the API route.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM projects WHERE id = ?', (project_id,))
            conn.execute('DELETE FROM project_files WHERE project_id = ?', (project_id,))
            try:
                from kairos.memory import doc_search
                doc_search.drop_project(conn, project_id)
            except Exception:
                pass

    def delete_project_memory(self, project_id: str) -> None:
        """Wipe every per-project table when the project is deleted."""
        with sqlite3.connect(self.db_path) as conn:
            for tbl in ('messages', 'loop_rounds', 'project_files', 'review_comments', 'ask_history', 'loop_checkpoints', 'project_preferences', 'project_notes', 'project_skills', 'working_fixes'):
                conn.execute(f'DELETE FROM {tbl} WHERE project_id = ?', (project_id,))
            try:
                conn.execute('DELETE FROM loop_rounds_fts WHERE project_id = ?', (project_id,))
            except sqlite3.OperationalError:
                pass
            try:
                from kairos.memory import doc_search
                doc_search.drop_project(conn, project_id)
            except Exception:
                pass

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
