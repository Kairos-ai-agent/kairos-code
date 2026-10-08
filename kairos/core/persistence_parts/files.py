"""Persistence mixin: FileStoreMixin, split out of kairos.core.persistence.

Reference-file storage plus the **document RAG index** (P0-4). File bodies are
stored inline (unchanged) and now also mirrored into a SQLite FTS5 index over
their content, so a project's documents can be *searched* instead of only
listed and inlined. See ``kairos/memory/doc_search.py`` for the retrieval core
(FTS5 probe, query building, ranking) -- this mixin owns the write path
(``add_file`` indexes incrementally, ``delete_file`` un-indexes) and the
per-project rebuild/search entry points. ``list_files``/``load_file`` keep
exactly their old behaviour.
"""
from __future__ import annotations
import logging
import sqlite3
import time
from typing import List, Optional

from kairos.memory import doc_search

logger = logging.getLogger(__name__)


class FileStoreMixin:
    def add_file(self, file_id: str, project_id: str, name: str, mime: str, size: int, content: str) -> None:
        """Insert one reference file. `content` may be text, base64 of
        binary, or any string — we don't try to be clever here.

        The row write is the source of truth; the FTS5 index over the same
        content is updated in the *same* connection so a search right after an
        upload finds it. A failure to index is logged, never fatal — losing the
        index must not lose the file.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('\n                INSERT OR REPLACE INTO project_files\n                (id, project_id, name, mime, size, content, uploaded_at)\n                VALUES (?, ?, ?, ?, ?, ?, ?)\n            ', (file_id, project_id, name, mime or 'application/octet-stream', int(size or 0), content or '', time.time()))
            try:
                doc_search.index_document(
                    conn, file_id, content or '',
                    project_id=project_id, name=name or '',
                )
            except Exception:
                logger.debug('project_files FTS index write failed', exc_info=True)

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
            removed = cur.rowcount > 0
            try:
                doc_search.drop_document(conn, file_id)
            except Exception:
                logger.debug('project_files FTS index delete failed', exc_info=True)
            return removed

    def load_all_files_for_project(self, project_id: str) -> List[dict]:
        """Return ALL reference files for a project, including content.
        Used by the orchestrator to build the Coder's prompt digest."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('\n                SELECT id, name, mime, size, content\n                FROM project_files WHERE project_id = ?\n                ORDER BY uploaded_at ASC\n            ', (project_id,)).fetchall()
            return [dict(r) for r in rows]

    # -- document RAG index (P0-4) ----------------------------------------

    def rebuild_file_index(self, project_id: Optional[str] = None) -> int:
        """(Re)build the content index from ``project_files``; return the count.

        Scope to one project with ``project_id`` or rebuild every project with
        ``None``. This is the explicit "rebuild index" entry point (also
        reachable as ``kairos index``); it is idempotent and safe to re-run.
        """
        with sqlite3.connect(self.db_path) as conn:
            if project_id:
                rows = conn.execute(
                    'SELECT id, project_id, name, content FROM project_files '
                    'WHERE project_id = ?', (project_id,)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT id, project_id, name, content FROM project_files'
                ).fetchall()
        docs = [
            {'file_id': r[0], 'project_id': r[1], 'name': r[2], 'content': r[3]}
            for r in rows
        ]
        with sqlite3.connect(self.db_path) as conn:
            if project_id:
                doc_search.ensure_index(conn)
                doc_search.drop_project(conn, project_id)
                for d in docs:
                    doc_search.index_document(
                        conn, d['file_id'], d['content'] or '',
                        project_id=project_id, name=d['name'] or '')
                return len(docs)
            return doc_search.rebuild(conn, docs)

    def _ensure_index_current(
        self, conn: sqlite3.Connection, project_id: Optional[str]
    ) -> int:
        """Index any files the index does not yet know about; return the delta.

        Keeps the "works by default" promise: a project whose files predate the
        index (or were written by an older build) is indexed on first search
        rather than silently returning nothing.
        """
        try:
            if project_id:
                total = conn.execute(
                    'SELECT count(*) FROM project_files WHERE project_id = ?',
                    (project_id,)).fetchone()[0]
            else:
                total = conn.execute(
                    'SELECT count(*) FROM project_files').fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        if not total:
            return 0
        indexed = doc_search.count_indexed(conn, project_id)
        if indexed >= total:
            return 0
        if project_id:
            rows = conn.execute(
                'SELECT id, name, content FROM project_files WHERE project_id = ?',
                (project_id,)).fetchall()
            doc_search.ensure_index(conn)
            doc_search.drop_project(conn, project_id)
            for fid, name, content in rows:
                doc_search.index_document(
                    conn, fid, content or '',
                    project_id=project_id, name=name or '')
            return len(rows)
        rows = conn.execute(
            'SELECT id, project_id, name, content FROM project_files').fetchall()
        docs = [
            {'file_id': r[0], 'project_id': r[1], 'name': r[2], 'content': r[3]}
            for r in rows
        ]
        doc_search.rebuild(conn, docs)
        return len(docs)

    def search_files_content(
        self, query: str, k: int = 5, *, project_id: Optional[str] = None
    ) -> dict:
        """Search reference-file **content**; return ranked hits with sources.

        Returns ``kairos.memory.doc_search.search``'s result dict: hits carry
        ``file`` / ``file_id`` / ``project_id`` / ``snippet`` / ``score`` /
        ``mode``, and ``status`` + ``note`` explain an empty result instead of
        returning a bare empty list. ``index`` names the active index kind
        (``fts5-trigram`` / ``fts5-unicode61`` / ``scan``); ``vector_enabled``
        and ``note`` state whether the neural re-rank path is on (it is off
        unless a genuinely neural embedder is configured).
        """
        with sqlite3.connect(self.db_path) as conn:
            try:
                healed = self._ensure_index_current(conn, project_id)
            except Exception:
                healed = 0
                logger.debug('project_files FTS auto-index failed', exc_info=True)
            result = doc_search.search(conn, query, k=k, project_id=project_id)
        if healed:
            result['auto_indexed'] = healed
        return result
