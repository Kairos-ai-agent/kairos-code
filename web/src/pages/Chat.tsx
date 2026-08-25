/**
 * Chat — the default landing page (replaces the old "Loop" tab).
 *
 * Two visual modes:
 *   1. Empty (no session yet): a centered hero with the composer
 *      and a couple of starter suggestions.
 *   2. Active (session selected or running): the thread on top,
 *      composer pinned to the bottom, and a topbar showing
 *      session title + round + score.
 *
 * On submit, we:
 *   1. POST /api/projects/{pid}/start with the requirement.
 *      The backend creates a new session_id, persists rounds as
 *      they land, and the WebSocket pushes messages into the chat
 *      store.
 *   2. The thread re-fetches /sessions and /sessions/{sid}/rounds
 *      on WebSocket "round.completed" events.
 *
 * On session select (sidebar click), we load the persisted rounds
 * for that session and let the WebSocket continue pushing new ones.
 */
import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Tag, Tooltip, message as antMessage, App as AntdApp } from 'antd';
import {
  PlayCircleOutlined, StopOutlined, ReloadOutlined, ThunderboltOutlined,
  ExportOutlined, CodeOutlined,
} from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import ChatThread from '../components/ChatThread';
import ChatComposer, { type ComposerMode } from '../components/ChatComposer';
import api, { onWebSocketMessage, onWebSocketState } from '../api/client';
import type { Message, LoopSession, SessionRound } from '../types';

const STARTER_SUGGESTIONS = [
  { title: '重构', text: '重构 src/api/server.py 的错误处理中间件，添加重试 + 限流' },
  { title: '测试', text: '为 kairos/teams.py 写单元测试，覆盖 timeout / exception / 合并三种路径' },
  { title: '文档', text: '给 kairos/agents_md.py 的 SkillsLoader 加中文 docstring' },
  { title: 'Bug', text: '检查 api/routes/projects.py 的 sessions 端点，确认 live session 排在最前' },
];

const Chat: React.FC = () => {
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const { message: msgApi } = AntdApp.useApp();

  const currentProject = useChatStore((s) => s.currentProject);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const setCurrentSessionId = useChatStore((s) => s.setCurrentSessionId);
  const currentMessages = useChatStore((s) => s.currentMessages);
  const setCurrentMessages = useChatStore((s) => s.setCurrentMessages);
  const appendMessage = useChatStore((s) => s.appendMessage);
  const setSessions = useChatStore((s) => s.setSessions);

  const [busy, setBusy] = useState(false);
  const [loopState, setLoopState] = useState<{
    running: boolean; round: number; last_score: number;
    last_approve: boolean; session_id?: string;
  } | null>(null);
  const [wsState, setWsState] = useState<'connecting' | 'open' | 'closed'>('closed');

  // ----- WebSocket plumbing -----
  useEffect(() => {
    const offMsg = onWebSocketMessage((data) => {
      // The orchestrator sends a JSON message for every agent message.
      // Anything that looks like a Message lands in the thread; loop
      // state updates (round, score) update the topbar; round.completed
      // triggers a sessions refetch.
      const t = (data.topic || data.type || '').toString();
      if (t === 'loop.round_completed' || t === 'round.completed') {
        // Refetch sessions so the sidebar reflects the new round.
        if (currentProject) {
          api.get<{ sessions: LoopSession[] }>(`/projects/${currentProject.id}/sessions`)
            .then((r) => setSessions(r.data.sessions || []))
            .catch(() => { /* offline / transient */ });
        }
        // Refetch loop state for the topbar.
        if (currentProject) {
          api.get(`/projects/${currentProject.id}/loop`).then((r) => {
            setLoopState(r.data);
            // If we're viewing this session, refetch its rounds.
            if (r.data?.session_id === sessionId) {
              loadSessionHistory(currentProject.id, r.data.session_id);
            }
          }).catch(() => {});
        }
      } else if (t === 'loop.session_completed' || t === 'session.completed') {
        // Loop reached its terminal state. Refresh sessions + state.
        if (currentProject) {
          api.get<{ sessions: LoopSession[] }>(`/projects/${currentProject.id}/sessions`)
            .then((r) => setSessions(r.data.sessions || []));
          api.get(`/projects/${currentProject.id}/loop`).then((r) => setLoopState(r.data));
        }
      } else if (t === 'agent.message' || t === 'message') {
        const m: Message = {
          id: data.id || `ws-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          sender: data.sender || 'agent',
          receiver: data.receiver || '',
          topic: data.topic || '',
          content: data.content ?? '',
          msg_type: data.msg_type || 'text',
          timestamp: data.timestamp || Date.now() / 1000,
          metadata: data.metadata || {},
        };
        appendMessage(m);
      } else if (t === 'loop.started' || t === 'session.started') {
        // Backend just kicked off a new session — push its id into
        // the chat store and let the page re-route to /chat/{sid}.
        const newSid = data.session_id || data.sessionId;
        if (newSid) {
          setCurrentSessionId(newSid);
          setCurrentMessages([]);
          // Refetch sessions so it shows in the sidebar.
          if (currentProject) {
            api.get<{ sessions: LoopSession[] }>(`/projects/${currentProject.id}/sessions`)
              .then((r) => setSessions(r.data.sessions || []));
          }
          // Update URL without re-mounting.
          navigate(`/chat/${newSid}`, { replace: true });
          // Re-fetch loop state for the topbar.
          if (currentProject) {
            api.get(`/projects/${currentProject.id}/loop`).then((r) => setLoopState(r.data))
              .catch(() => {});
          }
        }
      }
    });
    const offState = onWebSocketState((s) => setWsState(s));
    return () => { offMsg(); offState(); };
  }, [currentProject, sessionId, navigate,
     setCurrentMessages, appendMessage, setCurrentSessionId, setSessions]);

  // ----- Load session history on mount / session change -----
  const loadSessionHistory = useCallback(async (pid: string, sid: string) => {
    try {
      const r = await api.get<{ rounds: SessionRound[] }>(
        `/projects/${pid}/sessions/${sid}/rounds`);
      const rounds = r.data.rounds || [];
      // Convert rounds into Message-like bubbles for the thread.
      const msgs: Message[] = [];
      for (const rd of rounds) {
        if (rd.coder_summary) {
          msgs.push({
            id: `${sid}-${rd.round}-coder`,
            sender: 'coder',
            receiver: 'user',
            topic: 'coder.summary',
            content: rd.coder_summary,
            msg_type: 'text',
            timestamp: rd.created_at,
            metadata: { round: rd.round },
          });
        }
        if (rd.review_summary || rd.score) {
          msgs.push({
            id: `${sid}-${rd.round}-reviewer`,
            sender: 'reviewer',
            receiver: 'coder',
            topic: 'reviewer.summary',
            content: rd.review_summary,
            msg_type: 'text',
            timestamp: rd.created_at,
            metadata: {
              round: rd.round,
              score: rd.score,
              approve: !!rd.approve,
            },
          });
        }
      }
      setCurrentMessages(msgs);
    } catch (e) {
      // 404 = no rounds yet, that's fine.
      setCurrentMessages([]);
    }
  }, [setCurrentMessages]);

  useEffect(() => {
    if (!currentProject) return;
    api.get(`/projects/${currentProject.id}/loop`).then((r) => setLoopState(r.data))
      .catch(() => setLoopState(null));
  }, [currentProject]);

  useEffect(() => {
    if (currentProject && sessionId) {
      loadSessionHistory(currentProject.id, sessionId);
    } else {
      setCurrentMessages([]);
    }
  }, [currentProject, sessionId, loadSessionHistory, setCurrentMessages]);

  // ----- Actions -----
  const startLoop = async (text: string, _mode: ComposerMode) => {
    if (!currentProject) {
      msgApi.warning('Pick a project first.');
      return;
    }
    setBusy(true);
    try {
      // Optimistic: render the user's bubble immediately.
      appendMessage({
        id: `user-${Date.now()}`,
        sender: 'user', receiver: 'agent', topic: 'user.input',
        content: text, msg_type: 'text',
        timestamp: Date.now() / 1000, metadata: {},
      });
      await api.post(`/projects/${currentProject.id}/start`, { requirement: text });
      // The backend pushes session.started via WS; our handler
      // navigates to /chat/{sid} and refetches sessions.
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'Failed to start loop');
    } finally {
      setBusy(false);
    }
  };

  const stopLoop = async () => {
    if (!currentProject) return;
    try {
      await api.post(`/projects/${currentProject.id}/stop`);
      msgApi.success('Loop stopped.');
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || 'Failed to stop');
    }
  };

  const isRunning = !!loopState?.running;
  const hasSession = !!sessionId || isRunning;
  const showComposer = !!currentProject;  // we always allow typing

  return (
    <div style={{ display: 'flex', flexDirection: 'column',
                  height: 'calc(100vh - 52px)' }}>
      {/* Topbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '8px 16px',
        borderBottom: `1px solid ${tokens.border}`,
        background: tokens.bgBase,
      }}>
        <div style={{
          fontWeight: 600, fontSize: 14, color: tokens.labelPrimary,
          minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
          whiteSpace: 'nowrap', flex: 1,
        }}>
          {hasSession
            ? (loopState?.session_id
                ? `Session ${loopState.session_id.slice(0, 8)}…`
                : (sessionId ? `Session ${sessionId.slice(0, 8)}…` : 'New session'))
            : (currentProject ? `Chat · ${currentProject.name}` : 'No project')}
        </div>
        {isRunning && (
          <Tag color="processing" icon={<ThunderboltOutlined />}>running</Tag>
        )}
        {loopState && loopState.round > 0 && (
          <Tag color={loopState.last_approve ? 'green' : 'orange'}>
            R{loopState.round} · score {loopState.last_score}
          </Tag>
        )}
        <Tooltip title={wsState === 'open' ? 'WebSocket connected' : `WS ${wsState}`}>
          <span style={{
            display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
            background: wsState === 'open' ? tokens.success
                     : wsState === 'connecting' ? tokens.warning : tokens.danger,
          }} />
        </Tooltip>
        {isRunning ? (
          <Button danger icon={<StopOutlined />}
                  onClick={stopLoop} size="small">Stop</Button>
        ) : hasSession ? (
          <Button icon={<ReloadOutlined />}
                  onClick={() => currentProject && api.get(`/projects/${currentProject.id}/loop`).then((r) => setLoopState(r.data))}
                  size="small">Refresh</Button>
        ) : null}
      </div>

      {/* Thread */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <ChatThread
          messages={currentMessages}
          emptyHint={
            !currentProject
              ? 'Select a project from the top bar to start chatting.'
              : isRunning
                ? 'Loop is running — round output will appear here.'
                : 'Type a task below; the Coder + Reviewer will iterate until approved.'
          }
        />
      </div>

      {/* Composer */}
      <ChatComposer
        onSubmit={startLoop}
        busy={busy}
        disabled={!showComposer}
        disabledHint="Select a project first."
      />

      {/* Starter suggestions (only when truly empty + no session) */}
      {!hasSession && currentMessages.length === 0 && currentProject && (
        <div style={{
          maxWidth: 768, margin: '0 auto 12px',
          display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)',
          gap: 8, padding: '0 16px',
        }}>
          {STARTER_SUGGESTIONS.map((s) => (
            <Button
              key={s.title}
              onClick={() => {
                // Just populate the composer — user still needs to
                // press Enter to send. This is more transparent than
                // auto-sending.
                const ev = new CustomEvent('kairos:composer:set', { detail: s.text });
                window.dispatchEvent(ev);
              }}
              style={{
                textAlign: 'left', height: 'auto', padding: '10px 12px',
                background: tokens.bgLay1, border: `1px solid ${tokens.border}`,
                color: tokens.labelPrimary, whiteSpace: 'normal',
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>{s.title}</div>
              <div style={{ fontSize: 12, color: tokens.labelTertiary,
                            marginTop: 2 }}>{s.text}</div>
            </Button>
          ))}
        </div>
      )}
    </div>
  );
};

export default Chat;
