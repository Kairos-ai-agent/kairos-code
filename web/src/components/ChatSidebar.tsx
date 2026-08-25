/**
 * ChatSidebar — left rail of the chat UI.
 *
 * Layout (DSH / ChatGPT beta style):
 *
 *   ┌──────────────────────────────┐
 *   │  [ +  New chat             ]  │  ← primary CTA
 *   │  ──────────────────────────  │
 *   │  Today                      │  ← group label
 *   │   • Loop review  · 3    ●   │  ← active session
 *   │   • Try /plan          85   │
 *   │  Yesterday                  │
 *   │   • Refactor API            │
 *   │  Previous 7 days            │
 *   │   • ...                     │
 *   │  Older                      │
 *   │   • ...                     │
 *   │  ──────────────────────────  │
 *   │   [empty space flex]        │  ← scroll if list grows
 *   └──────────────────────────────┘
 *
 * Sessions are grouped into 4 buckets by `last_activity`:
 *   - Today (last 24h)
 *   - Yesterday (24-48h)
 *   - Previous 7 days (2-7d)
 *   - Older (>7d)
 *
 * The active session is highlighted via the chat store's
 * `currentSessionId`. Selecting a session updates the store and the
 * Chat page re-renders with that session's history.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Spin, Empty, Tooltip } from 'antd';
import { PlusOutlined, MessageOutlined, ThunderboltOutlined,
         CheckCircleFilled, CloseCircleFilled } from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import api from '../api/client';
import type { LoopSession } from '../types';

const ChatSidebar: React.FC = () => {
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const currentProject = useChatStore((s) => s.currentProject);
  const sessions = useChatStore((s) => s.sessions);
  const setSessions = useChatStore((s) => s.setSessions);
  const setCurrentSessionId = useChatStore((s) => s.setCurrentSessionId);
  const [loading, setLoading] = useState(false);

  // Re-fetch sessions whenever the project changes.
  useEffect(() => {
    if (!currentProject) {
      setSessions([]);
      return;
    }
    setLoading(true);
    api.get<{ sessions: LoopSession[] }>(`/projects/${currentProject.id}/sessions`)
      .then((r) => setSessions(r.data.sessions || []))
      .catch(() => setSessions([]))
      .finally(() => setLoading(false));
  }, [currentProject, setSessions]);

  // Group by date bucket. Order: Today → Yesterday → Previous 7 days → Older.
  const grouped = useMemo(() => groupByDate(sessions), [sessions]);

  const select = (sid: string) => {
    setCurrentSessionId(sid);
    navigate(`/chat/${sid}`);
  };

  const startNew = () => {
    if (!currentProject) {
      navigate('/today');
      return;
    }
    setCurrentSessionId(null);
    navigate('/chat');
  };

  return (
    <div style={{
      height: `calc(100vh - 52px)`,
      display: 'flex', flexDirection: 'column',
      padding: '12px 8px',
      boxSizing: 'border-box',
    }}>
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={startNew}
        block
        style={{
          background: tokens.labelPrimary, color: tokens.bgBase,
          border: 'none', fontWeight: 500,
          marginBottom: 12,
        }}
      >
        New chat
      </Button>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 4px' }}>
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'center',
                        padding: 16 }}>
            <Spin size="small" />
          </div>
        )}
        {!loading && sessions.length === 0 && (
          <Empty
            image={<MessageOutlined style={{ fontSize: 28,
                                            color: tokens.labelTertiary }} />}
            imageStyle={{ height: 40 }}
            description={
              <span style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                {currentProject
                  ? 'No sessions yet — start a new loop below.'
                  : 'Select a project to see its sessions.'}
              </span>
            }
            style={{ marginTop: 24 }}
          />
        )}
        {!loading && grouped.map((group) => (
          <div key={group.label} style={{ marginBottom: 12 }}>
            <div style={{
              padding: '6px 8px 4px', fontSize: 11,
              fontWeight: 600, color: tokens.labelTertiary,
              letterSpacing: '0.04em', textTransform: 'uppercase',
            }}>
              {group.label}
            </div>
            {group.items.map((s) => (
              <SessionRow
                key={s.session_id}
                session={s}
                active={s.session_id === sessionId}
                onClick={() => select(s.session_id)}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
};

const SessionRow: React.FC<{
  session: LoopSession;
  active: boolean;
  onClick: () => void;
}> = ({ session, active, onClick }) => {
  const tokens = useThemeTokens();
  // Title = first line of the coder summary, truncated.
  const title = useMemo(() => {
    if (!session.round_count) {
      return session.running ? 'Starting…' : 'Empty session';
    }
    return `Loop · ${session.round_count} round${session.round_count === 1 ? '' : 's'}`;
  }, [session]);

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onClick(); }}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 10px', borderRadius: 8,
        cursor: 'pointer',
        background: active ? tokens.bgLay2 : 'transparent',
        color: active ? tokens.labelPrimary : tokens.labelSecondary,
        transition: 'background 0.12s',
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = tokens.bgLay1;
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = 'transparent';
      }}
    >
      {session.running
        ? <ThunderboltOutlined style={{ color: tokens.coderAccent }} />
        : session.round_count === 0
          ? <MessageOutlined style={{ color: tokens.labelTertiary }} />
          : session.last_approve
            ? <CheckCircleFilled style={{ color: tokens.success }} />
            : <CloseCircleFilled style={{ color: tokens.warning }} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, fontWeight: active ? 600 : 500,
          whiteSpace: 'nowrap', overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {title}
        </div>
        {session.round_count > 0 && (
          <div style={{
            fontSize: 11, color: tokens.labelTertiary,
            whiteSpace: 'nowrap', overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}>
            {`R${session.last_round} · score ${session.last_score}`}
          </div>
        )}
      </div>
    </div>
  );
};

interface Group { label: string; items: LoopSession[]; }

function groupByDate(sessions: LoopSession[]): Group[] {
  const now = Date.now() / 1000;
  const oneDay = 24 * 3600;
  const buckets: Record<string, LoopSession[]> = {
    'Today': [], 'Yesterday': [], 'Previous 7 days': [], 'Older': [],
  };
  for (const s of sessions) {
    const age = now - (s.last_activity || s.started_at || 0);
    if (age < oneDay) buckets['Today'].push(s);
    else if (age < 2 * oneDay) buckets['Yesterday'].push(s);
    else if (age < 7 * oneDay) buckets['Previous 7 days'].push(s);
    else buckets['Older'].push(s);
  }
  const order = ['Today', 'Yesterday', 'Previous 7 days', 'Older'];
  return order
    .filter((k) => buckets[k].length > 0)
    .map((k) => ({ label: k, items: buckets[k] }));
}

export default ChatSidebar;
