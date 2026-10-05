"""Persistence mixin: MessageStoreMixin, split out of kairos.core.persistence."""
from __future__ import annotations
import json
import sqlite3
from typing import List, Optional


class MessageStoreMixin:
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

    def load_messages(self, limit: int=100, project_id: Optional[str]=None,
                      chat_only: bool=False, before_ts: float=0.0,
                      before_id: str='') -> List[dict]:
        """Load recent messages, optionally scoped to a single project.

        ``chat_only`` keeps only conversation bubbles (``CHAT_TOPICS``).

        ``before_ts`` / ``before_id`` form a keyset cursor: when set, only
        messages strictly older than that (timestamp, id) pair are returned,
        so callers can page back through the entire history instead of only
        ever seeing the newest ``limit`` rows.
        """
        sql = 'SELECT * FROM messages'
        where: List[str] = []
        params: List = []
        if project_id is not None:
            where.append('project_id = ?')
            params.append(project_id)
        if chat_only:
            where.append('topic IN (%s)' % ','.join('?' * len(self.CHAT_TOPICS)))
            params.extend(self.CHAT_TOPICS)
        if before_ts:
            where.append('(timestamp < ? OR (timestamp = ? AND id < ?))')
            params.extend([before_ts, before_ts, before_id])
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY timestamp DESC, id DESC LIMIT ?'
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]

    def load_events(self, project_id: str, topics: List[str],
                    limit: int = 4000) -> List[dict]:
        """Load a project's messages filtered to ``topics``, OLDEST first.

        Unlike :meth:`load_messages` (newest-first, paged with a cursor)
        this is the "replay" view the Task tracker needs: the loop
        lifecycle events (``loop.started`` / ``loop.coder_started`` /
        ``task.result`` …) that describe a long task's rounds. Because
        a busy project stores tens of thousands of ``stream.chunk``
        rows, the topic filter is essential — without it the events we
        want would fall outside any reasonable ``LIMIT``.
        """
        if not topics:
            return []
        placeholders = ','.join('?' * len(topics))
        sql = ('SELECT * FROM messages WHERE project_id = ? '
               'AND topic IN (%s) '
               'ORDER BY timestamp ASC, id ASC LIMIT ?' % placeholders)
        params: List = [project_id, *topics, limit]
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]
