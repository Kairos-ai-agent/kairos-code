"""Persistence mixin: FileStoreMixin, split out of kairos.core.persistence."""
from __future__ import annotations
import sqlite3
import time
from typing import List, Optional


class FileStoreMixin:
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
