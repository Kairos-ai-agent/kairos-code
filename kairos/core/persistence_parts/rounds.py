"""Persistence mixin: RoundStoreMixin, split out of kairos.core.persistence."""
from __future__ import annotations
import json
import logging
import sqlite3
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


class RoundStoreMixin:
    def save_loop_round(self, project_id: str, session_id: str, round_no: int, coder_summary: str, review: dict) -> None:
        """Persist one round's structured digest. Idempotent on
        (project_id, session_id, round) — if you save twice, the row is
        overwritten (the table's PRIMARY KEY makes INSERT OR REPLACE work).

        The full summaries are stored WITHOUT truncation so old rounds
        remain fully readable when the user scrolls history (a hard
        [:5000]/[:2000] slice silently cut long replies / code blocks)."""
        review_json = json.dumps(review, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            insert_order = self._next_loop_insert_order(conn)
            conn.execute('INSERT OR REPLACE INTO loop_rounds (project_id, session_id, round, coder_summary, review_summary, review_json, score, approve, created_at, insert_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (project_id, session_id, round_no, coder_summary, (review.get('summary') or ''), review_json, int(review.get('score') or 0), 1 if review.get('approve') else 0, time.time(), insert_order))

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

    def list_loop_sessions(self, project_id: str) -> List[dict]:
        """Distinct sessions for a project, newest session first.

        Each entry: {session_id, round_count, last_round, last_score,
        last_approve, started_at, ended_at}. The sidebar of the new
        chat-style UI uses this to render the conversation list.

        Sort: most recent activity first. We use MAX(created_at) over
        the session's rounds as the "last activity" timestamp; if a
        session has zero rounds (i.e. started but no round saved yet)
        it won't appear here, but the in-memory `project.loop_session`
        will still surface it through the loop endpoint.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('''
                SELECT
                    session_id,
                    COUNT(*)             AS round_count,
                    MAX(round)           AS last_round,
                    MAX(COALESCE(score, 0)) AS last_score,
                    MAX(approve)         AS last_approve,
                    MIN(created_at)      AS started_at,
                    MAX(created_at)      AS last_activity
                FROM loop_rounds
                WHERE project_id = ?
                GROUP BY session_id
                ORDER BY last_activity DESC
            ''', (project_id,)).fetchall()
        out: List[dict] = []
        for r in rows:
            d = dict(r)
            d['last_approve'] = bool(d.get('last_approve'))
            out.append(d)
        return out

    def load_session_rounds(self, project_id: str, session_id: str) -> List[dict]:
        """All rounds for one session, oldest first.

        Used by the chat thread to rebuild the full conversation
        history of a session. Same sort key as `load_loop_rounds`."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('''
                SELECT * FROM loop_rounds
                WHERE project_id = ? AND session_id = ?
            ''', (project_id, session_id)).fetchall()
        def _sort_key(r):
            # insert_order is a per-row monotonic counter; tie-breaking
            # on it (rather than `round`) keeps the order stable when
            # many rows share a created_at (batch saves / unit tests).
            return (r.get('created_at') or 0, r.get('insert_order') or 0)
        return sorted([dict(r) for r in rows], key=_sort_key)

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
                rows = conn.execute('SELECT lr.round, lr.coder_summary, lr.review_summary, fts.issues_text, fts.rank AS score FROM loop_rounds_fts fts JOIN loop_rounds lr ON     lr.project_id = fts.project_id     AND lr.session_id = fts.session_id     AND lr.round = fts.round WHERE fts.project_id = ? AND loop_rounds_fts MATCH ? ORDER BY fts.rank LIMIT ?', (project_id, query, limit)).fetchall()
                if rows:
                    return [dict(r) for r in rows]
                # JOIN found nothing in loop_rounds (caller only indexed
                # into FTS without saving the canonical round). Fall back
                # to FTS-only so the search still returns useful hits.
                rows = conn.execute('SELECT round, coder_summary, review_summary, issues_text FROM loop_rounds_fts WHERE project_id = ? AND loop_rounds_fts MATCH ? ORDER BY rank LIMIT ?', (project_id, query, limit)).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                logger.debug('FTS query failed for %r', query, exc_info=True)
                rows = conn.execute('SELECT round, coder_summary, review_summary FROM loop_rounds WHERE project_id = ? ORDER BY round DESC LIMIT ?', (project_id, limit)).fetchall()
                return [dict(r) for r in rows]
