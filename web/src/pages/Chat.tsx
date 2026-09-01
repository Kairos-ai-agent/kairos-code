import { formatError } from '../utils/formatError';
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
  ExportOutlined, CodeOutlined, BranchesOutlined,
} from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import ChatThread from '../components/ChatThread';
import ChatComposer from '../components/ChatComposer';
import { classifyIntent } from '../utils/intent';
import api, { onWebSocketMessage, onWebSocketState } from '../api/client';
import type { Message, LoopSession, SessionRound } from '../types';

// R38.6.3: removed STARTER_SUGGESTIONS — 4 hardcoded Chinese
// starter chips (重构 / 测试 / 文档 / Bug) referenced files
// (server.py, teams.py) that have nothing to do with the
// user's actual project. Cluttering the empty chat without
// adding value. The composer placeholder is enough.

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
  const appendStreamChunk = useChatStore((s) => s.appendStreamChunk);
  const finalizeStream = useChatStore((s) => s.finalizeStream);
  const setSessions = useChatStore((s) => s.setSessions);

  const [busy, setBusy] = useState(false);
  const [loopState, setLoopState] = useState<{
    running: boolean; round: number; last_score: number;
    last_approve: boolean; session_id?: string;
  } | null>(null);
  const [planState, setPlanState] = useState<{
    pending: boolean; text: string; decision: string | null;
    round: number;
  } | null>(null);
  const [askState, setAskState] = useState<{
    pending: boolean; question: string; context: string; round: number;
  } | null>(null);
  const [wsState, setWsState] = useState<'connecting' | 'open' | 'closed'>('closed');
  // component state is captured at render time and goes stale by the
  // time a chat reply returns, which used to render the single-turn
  // reply twice (once from the WS `agent.chat` event, once from the
  // REST response append below).
  // Last (project, session) whose history we hydrated. The loop
  // lifecycle handler loads history directly and then navigates,
  // which re-triggers the effect below — without this guard the
  // same session's rounds are fetched twice in a row.
  const lastLoadedRef = useRef('');

  // ----- Helpers -----
  // Load one session's full history from the backend and rehydrate
  // the chat thread with Message-like bubbles.
  const loadSessionHistory = useCallback(async (pid: string, sid: string) => {
    lastLoadedRef.current = `${pid}:${sid}`;
    try {
      const r = await api.get<{ rounds: SessionRound[] }>(
        `/projects/${pid}/sessions/${sid}/rounds`);
      const rounds = r.data.rounds || [];
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
      // 404 / network error = no rounds yet. R38.6.4: don't
      // wipe currentMessages — the local store already has the
      // user's chat bubbles (and possibly the agent's streamed
      // reply), which is what they want to see. The backend
      // session-rounds API only knows about loop rounds, not
      // plain chat messages. Replacing the store with [] here
      // was the root cause of "刷新后聊天记录还是没有了".
    }
  }, [setCurrentMessages]);

  // ----- WebSocket plumbing -----
  //
  // The backend sends three envelope types:
  //   { type: "init", agents, messages }       — sent on connect
  //   { type: "agent_update", agents }        — agent state refresh
  //   { type: "activity", message }           — agent activity event
  //
  // Activity events carry a `message` whose own `topic` field is the
  // actual event name (e.g. "loop.coder_started", "agent.response",
  // "tool.call"). The Loop.tsx page is the reference for the full
  // topic vocabulary.
  useEffect(() => {
    const offMsg = onWebSocketMessage((data) => {
      const envType = (data.type || '').toString();

      // ----- init / agent_update -----
      if (envType === 'init' || envType === 'agent_update') {
        return;  // nothing chat-specific to do; legacy Loop uses these
      }

      if (envType !== 'activity' || !data.message) {
        return;  // unknown envelope; ignore
      }

      // ----- activity: dispatch on the inner topic -----
      const msg = data.message;
      const topic = (msg.topic || '').toString();

      // R38.6.3: filter out the per-turn "Turn X/Y: reasoning..."
      // chatter. It's a status message for the Coder's tool loop,
      // not a chat bubble — surfacing it in the chat thread
      // clutters the conversation with progress noise. The Workbench
      // progress display subscribes to ``agent.progress`` for the
      // same data. Here we just don't add it to the chat.
      if (topic === 'agent.progress') return;
      const content = (msg.content ?? '').toString();
      const isTurnProgress = /^Turn \d+\/\d+:\s*reasoning/i.test(content);

      // R38.6.3: skip ``agent.response`` and ``task.result`` here.
      // Both events carry the same final text that the
      // ``stream.chunk`` events already streamed into a single
      // bubble. Without this filter the user sees the same
      // response 3 times (stream.chunk bubble + agent.response
      // bubble + task.result bubble). Terminal events for these
      // topics are handled below (line ~217) — ``finalizeStream``
      // closes the stream bubble. We don't append them.
      if (topic === 'agent.response' || topic === 'task.result') {
        // fall through to the lifecycle handlers below
      } else if (topic === 'agent.thinking' && !isTurnProgress) {
        // (handled below)
      } else if (topic === 'agent.chat' || topic === 'tool.call'
          || topic === 'tool.result' || topic === 'task.error') {
        // (handled below)
      } else {
        return;  // unknown topic — don't render a bubble
      }

      // User-visible chat bubbles: any agent activity that's worth
      // showing in the thread. Topics we surface:
      //   agent.thinking  — Coder started a new turn
      //   agent.chat       — generic agent chat (e.g. reviewer ask)
      //   tool.call        — tool invocation
      //   tool.result      — tool returned
      //   task.error       — agent hit an error
      //   stream.chunk     — streaming text delta (collapse into one bubble)
      //   agent.response / task.result are SKIPPED here — they're
      //   just terminal markers for the same content already shown
      //   via stream.chunk. They get their ``finalizeStream`` call
      //   below.
      if ((topic === 'agent.thinking' && !isTurnProgress)
          || topic === 'agent.chat' || topic === 'tool.call'
          || topic === 'tool.result' || topic === 'task.error') {
        // Dedupe: for single-turn chat the REST response is appended
        // by handleSubmit as well (topic agent.chat_reply). If that
        // bubble already landed with the same content, don't append
        // the WS copy — show the reply exactly once.
        //
        // R38.6.4: the previous window (15s) was too tight. The Coder
        // sometimes publishes the WS event before the REST response
        // comes back, and vice versa, with enough delay that the
        // timestamp comparison could miss. We now dedupe on content
        // match alone (no timestamp) — the content is unique enough
        // (a multi-sentence LLM reply) that a same-content match
        // within a 60s window is almost certainly the same reply
        // arriving via two paths.
        if (topic === 'agent.chat' || topic === 'agent.chat_reply') {
          const wsContent = typeof msg.content === 'string'
                              ? msg.content.trim() : '';
          if (wsContent) {
            const dup = useChatStore.getState().currentMessages.some(
              (m) => {
                const mContent = typeof m.content === 'string'
                                   ? m.content.trim() : '';
                if (mContent !== wsContent) return false;
                // Sender may be a full agent_id like "63bebf36.coder"
                // (from the message bus) or the short form "coder"
                // (from REST). Accept any of:
                //   "agent" | "coder" | "assistant" |
                //   endsWith(".coder") | endsWith(".reviewer") |
                //   includes("coder") | includes("reviewer")
                const s = (m.sender || '').toLowerCase();
                return s === 'agent' || s === 'coder' || s === 'assistant'
                  || s.endsWith('.coder') || s.endsWith('.reviewer')
                  || s.includes('coder') || s.includes('reviewer');
              });
            if (dup) return;
          }
        }
        appendMessage({
          id: msg.id || `ws-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          sender: msg.sender || 'agent',
          receiver: msg.receiver || '',
          topic,
          content: msg.content ?? '',
          msg_type: msg.msg_type || 'text',
          timestamp: msg.timestamp || Date.now() / 1000,
          metadata: msg.metadata || {},
        });
        return;
      }

      if (topic === 'stream.chunk') {
        // R38.6: stream chunks collapse into a single bubble per
        // sender. The Coder publishes one stream.chunk per token
        // (each Chinese char / English word), so naively appending
        // each chunk as its own message produces 8+ bubbles for a
        // 5-word reply. We delegate to ``appendStreamChunk`` which
        // finds the most recent stream bubble for this sender and
        // appends; if none exists, it creates one.
        const chunkContent = typeof msg.content === 'string'
          ? msg.content
          : JSON.stringify(msg.content || '');
        appendStreamChunk(
          msg.sender || 'agent',
          chunkContent,
          {
            topic,
            receiver: msg.receiver || '',
            metadata: msg.metadata || {},
            timestamp: msg.timestamp || Date.now() / 1000,
          },
        );
        return;
      }

      // Terminal events for the streaming bubble. We don't need to
      // do anything visual (the stream bubble already has the full
      // text), but we call finalizeStream so a new stream.chunk
      // (next turn) starts a fresh bubble instead of appending to
      // the now-finalized one.
      //
      // R38.6.4: also surface a task-completion toast. Previously
      // the user only saw the result by scrolling the chat; if
      // they had switched tabs or were in another part of the app
      // the agent's finish was silent. Now: task.result → "Done"
      // success toast, task.error → "Failed" error toast. The
      // toast text is short so it doesn't drown out the chat
      // content (the bubble carries the actual output).
      if (topic === 'agent.response' || topic === 'task.result' || topic === 'task.error') {
        finalizeStream(msg.sender || 'agent');
        if (topic === 'task.result') {
          const isErr = /error|fail|exception|traceback/i.test(content);
          if (isErr) {
            msgApi.error('Task failed — see the chat for details');
          } else {
            msgApi.success('Task done');
          }
        } else if (topic === 'task.error') {
          msgApi.error(`Task error: ${content.slice(0, 120)}`);
        }
      }

      // Loop lifecycle.
      if (topic === 'loop.coder_started' || topic === 'loop.plan_started'
          || topic === 'loop.completed' || topic === 'loop.finished'
          || topic === 'loop.approved' || topic === 'loop.rejected') {
        // The session is now running (or done). Refetch loop state
        // for the topbar and refresh the session list so the sidebar
        // picks up the new entry.
        if (currentProject) {
          // The message metadata usually carries session_id; use it
          // directly so the URL switches without waiting for the
          // /loop GET. Fall back to the GET when the message doesn't
          // have it (loop.finished, loop.completed, etc.).
          const wsSid = msg.metadata?.session_id;
          if (wsSid && wsSid !== sessionId) {
            setCurrentSessionId(wsSid);
            navigate(`/chat/${wsSid}`, { replace: true });
            loadSessionHistory(currentProject.id, wsSid);
          }
          api.get<{ sessions: LoopSession[] }>(`/projects/${currentProject.id}/sessions`)
            .then((r) => setSessions(r.data.sessions || []))
            .catch(() => {});
          api.get(`/projects/${currentProject.id}/loop`).then((r) => {
            setLoopState(r.data);
            const fetchedSid = r.data?.session_id;
            if (fetchedSid && !wsSid) {
              // The WS message didn't carry session_id; use the
              // fetched one. Switch the URL if it's new.
              if (fetchedSid !== sessionId) {
                setCurrentSessionId(fetchedSid);
                navigate(`/chat/${fetchedSid}`, { replace: true });
                loadSessionHistory(currentProject.id, fetchedSid);
              } else {
                loadSessionHistory(currentProject.id, fetchedSid);
              }
            }
          }).catch(() => {});
        }
        // Plan / Ask state — the orchestrator doesn't publish these
        // over WS, so the polling effect below picks them up.
        return;
      }

      // All other topics (loop.* details, precheck.*, regression.*,
      // cost_cap, safety_cap, etc.) are surfaced by the session
      // list / topbar / Trace page — we don't render them in the
      // thread itself.
    });
    const offState = onWebSocketState((s) => setWsState(s));
    return () => { offMsg(); offState(); };
  }, [currentProject, sessionId, navigate,
     setCurrentMessages, appendMessage, setCurrentSessionId, setSessions,
     loadSessionHistory]);

  useEffect(() => {
    if (!currentProject) return;
    api.get(`/projects/${currentProject.id}/loop`).then((r) => setLoopState(r.data))
      .catch(() => setLoopState(null));
  }, [currentProject]);

  // Poll for pending plan / ask every 2s when a project is selected.
  // Cheaper than wiring a dedicated WS topic for these — the loop
  // is short-lived and the calls are tiny.
  useEffect(() => {
    if (!currentProject) {
      setPlanState(null);
      setAskState(null);
      return;
    }
    const tick = () => {
      api.get<{ pending: boolean; text: string; decision: string | null;
                round: number }>(`/projects/${currentProject.id}/plan`)
        .then((r) => setPlanState(r.data || null))
        .catch(() => {});
      api.get<{ pending: boolean; question: string; context: string;
                round: number }>(`/projects/${currentProject.id}/ask`)
        .then((r) => setAskState(r.data || null))
        .catch(() => {});
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => window.clearInterval(id);
  }, [currentProject]);

  useEffect(() => {
    if (currentProject && sessionId) {
      const key = `${currentProject.id}:${sessionId}`;
      if (lastLoadedRef.current !== key) {
        loadSessionHistory(currentProject.id, sessionId);
      }
    } else {
      // R38.6.4: don't wipe currentMessages here. The user
      // could be on /chat (no sessionId) with chat-only bubbles
      // already in the store. Wiping them on mount was the root
      // cause of "刷新后聊天记录还是没有了". loadSessionHistory
      // also no longer clears the store on 404 (see its catch
      // block above), so the local chat thread survives across
      // refresh + project re-mount.
      lastLoadedRef.current = '';
    }
  }, [currentProject, sessionId, loadSessionHistory, setCurrentMessages]);

  // ----- Actions -----
  //
  // R38.6: the manual ``runAsTask`` toggle is gone. The composer
  // (ChatComposer) auto-classifies intent via utils/intent.ts and
  // we route accordingly:
  //   - intent='task'  → POST /start (full Coder ↔ Reviewer loop)
  //   - intent='chat'  → POST /chat  (single-turn reply)
  //   - askState.pending always wins (answer the reviewer's question).
  const handleSubmit = async (text: string) => {
    if (!currentProject) {
      msgApi.warning('Pick a project or folder first.');
      return;
    }
    setBusy(true);
    try {
      // Optimistic: render the user's bubble immediately.
      appendMessage({
        id: `user-${Date.now()}`,
        sender: 'user',
        receiver: askState?.pending ? 'reviewer' : 'agent',
        topic: askState?.pending ? 'ask.answer' : 'user.input',
        content: text, msg_type: 'text',
        timestamp: Date.now() / 1000, metadata: {},
      });
      if (askState?.pending) {
        await api.post(`/projects/${currentProject.id}/ask/answer`,
                       { answer: text });
        setAskState(null);
      } else if (classifyIntent(text) === 'task') {
        await api.post(`/projects/${currentProject.id}/start`,
                       { requirement: text });
      } else {
        // Single-turn chat. POST and wait for the reply, then
        // append it as a coder bubble so the thread reads like a
        // conversation. No loop is started.
        const r = await api.post<{ reply: string; mode: string }>(
          `/projects/${currentProject.id}/chat`, { message: text });
        const reply = (r.data?.reply || '').trim();
               if (reply) {
          // The Coder also publishes the reply over the WebSocket
          // (topic `agent.chat`), which the WS handler renders as a
          // bubble. To avoid showing the reply twice we only append
          // here when the WS bubble hasn't already landed (content
          // match) — the REST reply stays the reliable fallback, so
          // the answer always appears even if the WS event is lost
          // or the socket is down.
          //
          // R38.6.4: dedupe by content alone (no timestamp window).
          // The previous 15s window was too tight — the Coder can
          // publish the WS event seconds before the REST response
          // returns, or vice versa, and the two paths would each
          // append a bubble.
          const replyTrim = reply.trim();
          const alreadyShown = useChatStore.getState().currentMessages.some(
            (m) => {
              const mContent = typeof m.content === 'string'
                                 ? m.content.trim() : '';
              if (mContent !== replyTrim) return false;
              // Accept short form ('coder') and full agent_id
              // ('63bebf36.coder') — both are the same agent.
              const s = (m.sender || '').toLowerCase();
              return s === 'agent' || s === 'coder' || s === 'assistant'
                || s.endsWith('.coder') || s.endsWith('.reviewer')
                || s.includes('coder') || s.includes('reviewer');
            });
          if (!alreadyShown) {
            appendMessage({
              id: `chat-reply-${Date.now()}`,
              sender: 'coder',
              receiver: 'user',
              topic: 'agent.chat_reply',
              content: reply,
              msg_type: 'text',
              timestamp: Date.now() / 1000,
              metadata: { mode: 'chat' },
            });
          }
        }
      }
    } catch (e: any) {
      // Show the REAL error from the server, not a generic
      // "Failed to submit" — the previous fallback hid useful
      // diagnostics (e.g. "Invalid API key" → user thought the
      // UI was broken, when it was actually a settings issue).
      console.error('[ChatComposer] submit failed:', e);
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      let msg: string;
      if (typeof detail === 'string' && detail.trim()) {
        msg = detail;
      } else if (Array.isArray(detail) && detail.length) {
        // FastAPI 422 validation errors come back as a list of
        // {loc, msg, type} objects. Pick the first msg.
        const first = detail[0];
        msg = (first?.msg && typeof first.msg === 'string')
              ? first.msg
              : JSON.stringify(detail);
      } else if (typeof detail === 'object' && detail !== null) {
        msg = JSON.stringify(detail);
      } else if (typeof e?.message === 'string' && e.message) {
        msg = e.message;
      } else {
        msg = 'Failed to submit';
      }
      if (status) {
        msg = `[${status}] ${msg}`;
      }
      // R38.6.4: actionable hint when the error looks like a
      // settings / Coder-not-ready issue. The user is more likely
      // to fix it when we point them at the next step.
      //
      // 404 needs a different hint — "Project not found" means the
      // tab's project_id is stale (e.g. backend was restarted and
      // the orchestrator's in-memory map hasn't caught up). Tell
      // the user to pick a project from the sidebar instead of
      // steering them at Settings (which wouldn't help).
      const isProjectMissing = /project not found/i.test(msg);
      const isNoCoder = /no coder/i.test(msg);
      if (isProjectMissing) {
        msg += ' — pick a different project from the sidebar.';
      } else if (isNoCoder) {
        // Don't blanket-suggest Settings — the backend's 503
        // detail now includes the real attach_errors (mcp/worktree/
        // provider init). The user needs to see those to fix it.
        // Only fall back to "open Settings" if no specific error
        // came through.
        if (!/attach errors?[: ]/i.test(msg)
            && !/mcp[: ]/i.test(msg)
            && !/worktree[: ]/i.test(msg)
            && !/provider[: ]/i.test(msg)) {
          msg += ' — open Settings → LLM Models and click Save.';
        }
      } else if (status === 503
          || /api[_ ]?key|provider|model|not configured|unauthorized|401/i.test(msg)) {
        msg += ' — open Settings → LLM Models and click Save.';
      }
      msgApi.error(msg);
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
        {hasSession && currentProject && sessionId && (
          <Tooltip title="View per-turn trace">
            <Button
              icon={<BranchesOutlined />}
              onClick={() => navigate(`/trace/${currentProject.id}/${sessionId}`)}
              size="small"
            >
              Trace
            </Button>
          </Tooltip>
        )}
      </div>

      {/* Thread */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <ChatThread
          messages={currentMessages}
          emptyHint={
            !currentProject
              ? 'Pick a project, or add a folder above, to start chatting.'
              : isRunning
                ? 'Loop is running — round output will appear here.'
                : 'Type a task below; the Auto router picks the right mode.'
          }
        />
      </div>

      {/* Plan / Ask banner — shows when the loop is waiting on the user */}
      {planState?.pending && (
        <PlanBanner
          text={planState.text}
          round={planState.round}
          onApprove={async () => {
            if (!currentProject) return;
            try {
              await api.post(`/projects/${currentProject.id}/plan/approve`);
              setPlanState(null);
            } catch (e: any) {
              msgApi.error(e?.response?.data?.detail || 'Failed to approve');
            }
          }}
          onReject={async () => {
            if (!currentProject) return;
            try {
              await api.post(`/projects/${currentProject.id}/plan/reject`);
              setPlanState(null);
            } catch (e: any) {
              msgApi.error(e?.response?.data?.detail || 'Failed to reject');
            }
          }}
        />
      )}
      {askState?.pending && (
        <AskBanner
          question={askState.question}
          context={askState.context}
          round={askState.round}
          onAnswered={() => setAskState(null)}
        />
      )}

      {/* Composer */}
      <ChatComposer
        onSubmit={handleSubmit}
        busy={busy}
        disabled={!showComposer}
        disabledHint="Select a project or folder to start."
      />
    </div>
  );
};

export default Chat;

// ---------------------------------------------------------------------------
// PlanBanner — shown when the Coder has produced a draft plan and the
// loop is blocked on user approval.
// ---------------------------------------------------------------------------

const PlanBanner: React.FC<{
  text: string;
  round: number;
  onApprove: () => Promise<void> | void;
  onReject: () => Promise<void> | void;
}> = ({ text, round, onApprove, onReject }) => {
  const tokens = useThemeTokens();
  const [busy, setBusy] = useState(false);
  const wrap = async (fn: () => Promise<void> | void) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };
  return (
    <div style={{
      margin: '0 16px 8px', maxWidth: 768, marginLeft: 'auto', marginRight: 'auto',
      background: tokens.bgLay1, border: `1px solid ${tokens.borderStrong}`,
      borderLeft: `4px solid ${tokens.coderAccent}`,
      borderRadius: 12, padding: 14,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        marginBottom: 8,
      }}>
        <span style={{ fontWeight: 600, fontSize: 13,
                       color: tokens.labelPrimary }}>
          📋 Plan ready · round {round}
        </span>
      </div>
      <pre style={{
        margin: 0, fontSize: 12, lineHeight: 1.5,
        color: tokens.labelSecondary, maxHeight: 160, overflow: 'auto',
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        fontFamily: 'inherit',
      }}>
        {text}
      </pre>
      <div style={{ display: 'flex', gap: 8, marginTop: 10, justifyContent: 'flex-end' }}>
        <Button size="small" onClick={() => wrap(onReject)} loading={busy}>
          Reject & stop
        </Button>
        <Button size="small" type="primary"
                style={{ background: tokens.coderAccent, border: 'none' }}
                onClick={() => wrap(onApprove)} loading={busy}>
          Approve & continue
        </Button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// AskBanner — shown when the Reviewer has asked a clarifying question
// and the loop is blocked on the user's answer.
// ---------------------------------------------------------------------------

const AskBanner: React.FC<{
  question: string;
  context: string;
  round: number;
  onAnswered: () => void;
}> = ({ question, context, round, onAnswered }) => {
  const tokens = useThemeTokens();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!text.trim()) return;
    // We need the currentProject id here; pull it from the chat store
    // so this component stays self-contained.
    const pid = useChatStore.getState().currentProject?.id;
    if (!pid) return;
    setBusy(true);
    try {
      await api.post(`/projects/${pid}/ask/answer`, { answer: text });
      onAnswered();
    } catch (e: any) {
      // surface error inline; the parent page also has its own
      // antMessage handler.
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{
      margin: '0 16px 8px', maxWidth: 768, marginLeft: 'auto', marginRight: 'auto',
      background: tokens.bgLay1, border: `1px solid ${tokens.borderStrong}`,
      borderLeft: `4px solid ${tokens.reviewerAccent}`,
      borderRadius: 12, padding: 14,
    }}>
      <div style={{
        fontWeight: 600, fontSize: 13, color: tokens.labelPrimary,
        marginBottom: 6,
      }}>
        ❓ Reviewer asks · round {round}
      </div>
      <div style={{ fontSize: 13, color: tokens.labelSecondary,
                    lineHeight: 1.5, marginBottom: 8 }}>
        {question}
      </div>
      {context && (
        <details style={{ marginBottom: 8, color: tokens.labelTertiary,
                          fontSize: 12 }}>
          <summary style={{ cursor: 'pointer' }}>Show context</summary>
          <pre style={{ whiteSpace: 'pre-wrap', marginTop: 6,
                        fontFamily: 'inherit' }}>
            {context}
          </pre>
        </details>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
          placeholder="Type your answer…"
          style={{
            flex: 1, padding: '6px 10px', borderRadius: 8,
            border: `1px solid ${tokens.border}`,
            background: tokens.bgBase, color: tokens.labelPrimary,
            fontSize: 13, outline: 'none',
          }}
          autoFocus
        />
        <Button type="primary" onClick={submit} loading={busy}
                disabled={!text.trim()}>Answer</Button>
      </div>
    </div>
  );
};

