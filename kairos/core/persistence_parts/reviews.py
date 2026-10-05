"""Persistence mixin: ReviewStoreMixin, split out of kairos.core.persistence."""
from __future__ import annotations
import json
import sqlite3
import time


class ReviewStoreMixin:
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
