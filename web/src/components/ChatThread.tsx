/**
 * ChatThread — the scrollable message list in the main area.
 *
 * The thread is organised by *turn*, not by message, because that is the unit
 * the user thinks in ("I asked, it worked, it answered"):
 *
 *   ┌ user: the question ────────────────────────────────┐
 *   ├ ▸ 过程 · 4 步 · 12.4s   (collapses the whole run)  │
 *   │   💭 thinking …                                    │
 *   │   🔧 read_file  ok  0.3s                           │
 *   │   🔧 run_tests  failed  8.1s                       │
 *   └ Kairos: the answer (markdown) ─────────────────────┘
 *
 * Why: the events an agent emits while working (agent.progress, tool.call,
 * tool.result, agent.thinking) used to land as flat bubbles between the
 * question and the answer, so a busy turn buried its own reply and the user
 * could not see *how* the answer was reached. Grouping keeps the sequence
 * visible without letting the transcript become a log file: the process block
 * is open while a turn is running and collapsed once it is done.
 *
 * Tool calls are paired with their results by (turn, tool) — the backend emits
 * both with those fields — so each step shows its own duration and outcome.
 * Nothing here invents a step: a message with no result yet renders as running.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Tag, Empty, Tooltip, Segmented } from 'antd';
import {
  UserOutlined, AuditOutlined, ToolOutlined, EditOutlined,
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined,
  BranchesOutlined, BulbOutlined, FileSearchOutlined, ThunderboltOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';
import type { Message } from '../types';
import { useChatStore } from '../stores/chatStore';
import { renderMarkdown, MONO_STACK, type MarkdownStyle } from '../utils/markdown';

interface Props {
  messages: Message[];
  emptyHint?: string;
  /** Show a "raw" toggle to dump the raw JSON instead of the parsed view. */
  showRawToggle?: boolean;
}

interface Step {
  kind: 'thinking' | 'tool';
  /** Tool name (tool steps only). */
  tool?: string;
  args?: string;
  /** null = no result arrived yet: the step is still running. */
  ok?: boolean | null;
  /** Seconds source: the call's own timestamp, set when the step is created. */
  startedAt: number;
  ms?: number;
  /** Free text: the thinking body, or the tool result. */
  text: string;
}

interface Turn {
  key: string;
  user?: Message;
  steps: Step[];
  reply?: Message;
  /** Additional bubbles that are neither user, process nor reply. */
  others: Message[];
}

// ------------------------------------------------------------------ grouping

const isProcessTopic = (m: Message): boolean => {
  const topic = (m.topic || '').toLowerCase();
  return topic === 'agent.progress' || topic === 'agent.thinking'
      || topic.startsWith('tool.');
};

const isReply = (m: Message): boolean => {
  const topic = (m.topic || '').toLowerCase();
  const sender = (m.sender || '').toLowerCase();
  if (isProcessTopic(m)) return false;
  if (sender === 'user' || sender === 'human') return false;
  return sender === 'agent' || sender.includes('coder')
      || sender.includes('planner') || sender.includes('specialist')
      || topic === 'agent.chat' || topic === 'agent.response';
};

/** Short one-line rendering of a tool's arguments for the step row. */
function summarizeArgs(meta: Record<string, unknown>, content: string): string {
  const fromMeta = meta?.args ?? meta?.arguments;
  if (fromMeta && typeof fromMeta === 'object') {
    try {
      const s = JSON.stringify(fromMeta);
      return s.length > 80 ? `${s.slice(0, 80)}…` : s;
    } catch { /* fall through to content */ }
  }
  // The backend writes "Calling name({...})" into content; take the part
  // inside the first parenthesis so the row shows the call, not the wrapper.
  const open = content.indexOf('(');
  const close = content.lastIndexOf(')');
  if (open >= 0 && close > open) {
    const inner = content.slice(open + 1, close);
    return inner.length > 80 ? `${inner.slice(0, 80)}…` : inner;
  }
  return '';
}

function groupIntoTurns(messages: Message[]): Turn[] {
  const turns: Turn[] = [];
  let current: Turn | null = null;
  const keyOf = (m: Message, i: number) => m.id || `i${i}`;

  messages.forEach((m, i) => {
    const sender = (m.sender || '').toLowerCase();
    if (sender === 'user' || sender === 'human') {
      current = { key: keyOf(m, i), user: m, steps: [], others: [] };
      turns.push(current);
      return;
    }
    if (isProcessTopic(m)) {
      if (!current) {
        current = { key: keyOf(m, i), steps: [], others: [] };
        turns.push(current);
      }
      const topic = (m.topic || '').toLowerCase();
      const meta = m.metadata || {};
      if (topic.startsWith('tool.')) {
        const tool = String(meta.tool || meta.name || 'tool');
        if (topic === 'tool.result') {
          // Attach to the oldest open call for this tool in this turn.
          const open = current.steps.find(
            (s) => s.kind === 'tool' && s.tool === tool && s.ok === null);
          const body = stringifyContent(m.content);
          if (open) {
            open.ok = meta.ok === false || meta.error ? false : true;
            open.ms = Math.max(0, (m.timestamp - open.startedAt) * 1000);
            open.text = body;
          } else {
            current.steps.push({
              kind: 'tool', tool, ok: true, text: body,
              startedAt: m.timestamp,
            });
          }
        } else {
          current.steps.push({
            kind: 'tool', tool, ok: null,
            args: summarizeArgs(meta, stringifyContent(m.content)),
            text: stringifyContent(m.content),
            startedAt: m.timestamp,
          });
        }
      } else if (topic === 'agent.progress') {
        // Progress chatter is not a step of its own — it would triple the
        // row count. It only tells us a turn is running, which the live row
        // already says.
      } else {
        current.steps.push({
          kind: 'thinking', ok: true, text: stringifyContent(m.content),
          startedAt: m.timestamp,
        });
      }
      return;
    }
    if (isReply(m) && current && !current.reply) {
      current.reply = m;
      return;
    }
    if (current) current.others.push(m);
    else {
      const t: Turn = { key: keyOf(m, i), steps: [], others: [m] };
      turns.push(t);
      current = t;
    }
  });
  return turns;
}

// ------------------------------------------------------------------ thread

const ChatThread: React.FC<Props> = ({ messages, emptyHint, showRawToggle = false }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const [view, setView] = useState<'pretty' | 'raw'>('pretty');
  const containerRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  // Detect manual scroll: if user scrolls up, stop auto-sticking.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      stickToBottomRef.current = distFromBottom < 80;
    };
    el.addEventListener('scroll', onScroll);
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Auto-scroll on new messages, after the new bubble has been painted (a
  // synchronous scrollTop read would still see the old scrollHeight).
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !stickToBottomRef.current) return;
    const raf = requestAnimationFrame(() => {
      const last = el.lastElementChild as HTMLElement | null;
      if (last && typeof last.scrollIntoView === 'function') {
        last.scrollIntoView({ block: 'end', inline: 'nearest' });
      } else {
        el.scrollTop = el.scrollHeight;
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [messages]);

  const turns = useMemo(() => groupIntoTurns(messages), [messages]);
  const mdStyle = useMemo<MarkdownStyle>(() => ({
    text: tokens.labelPrimary,
    muted: tokens.labelTertiary,
    codeBackground: tokens.toolBubble,
    codeColor: tokens.labelPrimary,
    border: tokens.border,
    link: tokens.coderAccent,
    mono: MONO_STACK,
  }), [tokens]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column',
                  height: 'calc(100vh - 52px)' }}>
      {showRawToggle && (
        <div style={{ display: 'flex', justifyContent: 'flex-end',
                      padding: '8px 16px 0' }}>
          <Segmented
            size="small"
            value={view}
            onChange={(v) => setView(v as 'pretty' | 'raw')}
            options={[{ label: t('chat.thread.viewPretty'), value: 'pretty' },
                       { label: t('chat.thread.viewRaw'), value: 'raw' }]}
          />
        </div>
      )}
      <div ref={containerRef} style={{ flex: 1, overflowY: 'auto',
                                       padding: '24px 16px 24px' }}>
        <div style={{ maxWidth: 768, margin: '0 auto' }}>
          {messages.length === 0 ? (
            <div style={{ display: 'flex', alignItems: 'center',
                          justifyContent: 'center', minHeight: '60vh' }}>
              <Empty
                image={<BranchesOutlined style={{ fontSize: 40,
                                                  color: tokens.labelTertiary }} />}
                description={
                  <span style={{ color: tokens.labelTertiary }}>
                    {emptyHint || t('chat.thread.empty')}
                  </span>
                }
              />
            </div>
          ) : view === 'raw' ? (
            <pre style={{
              background: tokens.toolBubble, color: tokens.labelPrimary,
              padding: 16, borderRadius: 8, overflow: 'auto',
              fontSize: 12, lineHeight: 1.5, fontFamily: MONO_STACK,
            }}>
              {JSON.stringify(messages, null, 2)}
            </pre>
          ) : (
            turns.map((turn) => <TurnView key={turn.key} turn={turn} mdStyle={mdStyle} />)
          )}
          {view !== 'raw' && <LiveStatusRow />}
        </div>
      </div>
    </div>
  );
};

export default ChatThread;

// ------------------------------------------------------------------ turn

const TurnView: React.FC<{ turn: Turn; mdStyle: MarkdownStyle }> = ({ turn, mdStyle }) => {
  // A turn with no reply yet is still running: its process stays open so the
  // user can watch the steps land, then folds away once the answer arrives.
  const running = !turn.reply;
  return (
    <div data-testid="chat-turn">
      {turn.user && <UserBubble message={turn.user} />}
      {turn.steps.length > 0 && <ProcessBlock steps={turn.steps} running={running} />}
      {turn.others.map((m, i) => (
        <MessageBubble key={m.id || i} message={m} mdStyle={mdStyle} />
      ))}
      {turn.reply && (
        <AssistantBubble message={turn.reply} mdStyle={mdStyle}
                         running={false} />
      )}
    </div>
  );
};

// ------------------------------------------------------------------ process

/** `0.4s` / `12.3s` / `1m 04s` — seconds are enough for a tool call. */
function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms < 0) return '';
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(Math.round(s - m * 60)).padStart(2, '0')}s`;
}

const ProcessBlock: React.FC<{ steps: Step[]; running: boolean }> = ({ steps, running }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const [open, setOpen] = useState(running);
  // Follow the turn: open while it runs, folded once it finishes — unless the
  // user has expressed a preference by toggling it themselves.
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current) setOpen(running);
  }, [running]);

  const total = steps.reduce((acc, s) => acc + (s.ms || 0), 0);
  const failed = steps.filter((s) => s.kind === 'tool' && s.ok === false).length;

  return (
    <div
      data-testid="process-block"
      style={{
        margin: '2px 0 10px 34px',
        border: `1px solid ${tokens.border}`,
        borderRadius: 8,
        background: tokens.bgLay1,
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        data-testid="process-toggle"
        aria-expanded={open}
        onClick={() => { touched.current = true; setOpen((v) => !v); }}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, width: '100%',
          padding: '5px 10px', background: 'transparent', border: 'none',
          cursor: 'pointer', textAlign: 'left',
          fontSize: 11.5, color: tokens.labelTertiary,
        }}
      >
        <span style={{ transform: open ? 'rotate(90deg)' : 'none',
                       transition: 'transform 0.12s', display: 'inline-block' }}>
          ▸
        </span>
        {running ? <LoadingOutlined spin style={{ fontSize: 11 }} />
                 : <ThunderboltOutlined style={{ fontSize: 11 }} />}
        <span>
          {t('chat.process.summary', {
            n: steps.length,
            time: formatMs(total) || '—',
          })}
        </span>
        {failed > 0 && (
          <Tag color="red" style={{ marginInlineStart: 'auto', marginInlineEnd: 0,
                                    fontSize: 10, lineHeight: '16px' }}>
            {t('chat.process.failedSteps', { n: failed })}
          </Tag>
        )}
      </button>
      {open && (
        <div style={{ padding: '0 10px 8px 10px' }}>
          {steps.map((s, i) => <StepRow key={i} step={s} />)}
        </div>
      )}
    </div>
  );
};

const StepRow: React.FC<{ step: Step }> = ({ step }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const [open, setOpen] = useState(false);

  const isThinking = step.kind === 'thinking';
  const running = step.ok === null;
  const bad = step.ok === false;

  const color = bad ? tokens.danger
    : running ? tokens.labelTertiary
    : isThinking ? tokens.coderAccent ?? tokens.labelSecondary
    : tokens.success;

  const icon = isThinking ? <BulbOutlined style={{ fontSize: 11 }} />
    : running ? <LoadingOutlined spin style={{ fontSize: 11, color: tokens.labelTertiary }} />
    : bad ? <CloseCircleOutlined style={{ fontSize: 11 }} />
    : <CheckCircleOutlined style={{ fontSize: 11 }} />;

  const label = isThinking
    ? t('chat.process.thinkingStep')
    : step.tool || 'tool';

  // A one-line preview so the row says WHAT it is doing, not just which tool
  // ran: for a thinking step that is the thought, and for a failed step it is
  // the reason. The full text stays one click away.
  const preview = (step.text || '').replace(/\s+/g, ' ').trim().slice(0, 140);

  // Long results stay behind a click: the sequence is what matters, and the
  // four-hundredth line of a file read is not.
  const body = step.text;
  const long = (body || '').length > 160;

  return (
    <div data-testid="process-step" style={{ fontSize: 11.5, marginBottom: 3 }}>
      <div
        onClick={() => long && setOpen((v) => !v)}
        style={{
          display: 'flex', alignItems: 'baseline', gap: 6,
          cursor: long ? 'pointer' : 'default',
          color: tokens.labelSecondary, minWidth: 0,
        }}
      >
        <span style={{ color, flexShrink: 0 }}>{icon}</span>
        <span style={{ fontWeight: 600, color: tokens.labelPrimary,
                       flexShrink: 0, fontFamily: MONO_STACK,
                       fontSize: 11 }}>{label}</span>
        {step.args && (
          <span style={{
            color: tokens.labelTertiary, fontFamily: MONO_STACK, fontSize: 10.5,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            minWidth: 0, flex: 1,
          }}>{step.args}</span>
        )}
        {!step.args && preview && (
          <span style={{
            color: bad ? tokens.danger : tokens.labelTertiary,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            minWidth: 0, flex: 1,
          }}>{preview}</span>
        )}
        <span style={{ marginInlineStart: 'auto', flexShrink: 0,
                       color: tokens.labelTertiary, fontSize: 10.5 }}>
          {running ? t('chat.process.running') : formatMs(step.ms)}
        </span>
      </div>
      {open && body && (
        <pre style={{
          margin: '4px 0 6px 17px', padding: '6px 8px',
          background: tokens.toolBubble, color: tokens.labelSecondary,
          borderRadius: 6, fontSize: 11, lineHeight: 1.5,
          fontFamily: MONO_STACK, whiteSpace: 'pre-wrap',
          wordBreak: 'break-word', maxHeight: 240, overflow: 'auto',
        }}>{body}</pre>
      )}
    </div>
  );
};

// ------------------------------------------------------------------ live status

const LiveStatusRow: React.FC = () => {
  const tokens = useThemeTokens();
  const t = useT();
  const live = useChatStore((s) => s.liveStatus);
  if (!live) return null;
  return (
    <div
      data-testid="live-status"
      style={{ display: 'flex', alignItems: 'center', gap: 8,
               margin: '10px 0 6px', fontSize: 12, color: tokens.labelTertiary }}
    >
      <LoadingOutlined spin style={{ fontSize: 12 }} />
      <span>
        {live.kind === 'tool'
          ? t('chat.status.tool', { tool: live.detail || 'tool' })
          : t('chat.status.thinking')}
      </span>
    </div>
  );
};

// ------------------------------------------------------------------ bubbles

const MessageBubble: React.FC<{ message: Message; mdStyle: MarkdownStyle }> = ({
  message, mdStyle,
}) => {
  const sender = (message.sender || '').toLowerCase();
  const topic = (message.topic || '').toLowerCase();
  if (sender === 'user' || sender === 'human') return <UserBubble message={message} />;
  if (topic === 'agent.thinking') {
    return <ThinkingBubble message={message} />;
  }
  if (topic.startsWith('tool.')) return <ToolBubble message={message} />;
  if (sender.includes('reviewer') || topic.includes('review')) {
    return <ReviewerBubble message={message} />;
  }
  if (isReply(message)) return <AssistantBubble message={message} mdStyle={mdStyle} />;
  return <SystemBubble message={message} />;
};

const UserBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
      <div style={{
        maxWidth: '85%', padding: '10px 14px',
        background: tokens.userBubble, color: tokens.labelPrimary,
        borderRadius: 16, fontSize: 14, lineHeight: 1.55,
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {stringifyContent(message.content)}
      </div>
    </div>
  );
};

function splitThinking(text: string): { thinking: string; body: string } {
  if (!text) return { thinking: '', body: text };
  const m = text.match(/<think>([\s\S]*?)<\/think>/i);
  if (!m) return { thinking: '', body: text };
  return { thinking: (m[1] || '').trim(),
           body: text.replace(m[0], '').trim() };
}

/**
 * The reply. Markdown is rendered (replies are written in it — code fences and
 * lists were showing up as literal characters), a leading ``<think>`` block is
 * folded above it, and the whole thing is one conversation bubble with the
 * Kairos mark rather than a bordered "role card".
 */
const AssistantBubble: React.FC<{
  message: Message;
  mdStyle: MarkdownStyle;
  running?: boolean;
}> = ({ message, mdStyle, running = false }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const raw = stringifyContent(message.content);
  const { thinking, body } = splitThinking(raw);
  const rendered = useMemo(() => renderMarkdown(body, mdStyle), [body, mdStyle]);
  const meta = message.metadata || {};
  const usage = (meta.usage || {}) as Record<string, number>;
  const tokensUsed = typeof usage.total_tokens === 'number' ? usage.total_tokens : null;

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8,
                  marginTop: 4, marginBottom: 6 }}>
      <div style={{
        width: 26, height: 26, borderRadius: 6,
        background: '#facc15', color: '#000',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontWeight: 900, fontSize: 14, flexShrink: 0, marginTop: 2,
      }}>K</div>
      <div style={{ flex: 1, minWidth: 0, background: tokens.bgElevated,
                    borderRadius: 8, padding: '8px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                      marginBottom: 2 }}>
          {tokensUsed !== null && (
            <Tooltip title={t('chat.thread.tokenUsage')}>
              <span style={{ fontSize: 10, color: tokens.labelTertiary,
                             marginInlineStart: 'auto' }}>
                {t('chat.thread.tokensUsed', { n: tokensUsed.toLocaleString() })}
              </span>
            </Tooltip>
          )}
        </div>
        {thinking && (
          <details
            data-testid="think-block"
            style={{ marginBottom: 6, background: tokens.bgLay1,
                     border: `1px solid ${tokens.border}`,
                     borderRadius: 6, padding: '4px 8px' }}
          >
            <summary style={{ cursor: 'pointer', fontSize: 11,
                              color: tokens.labelTertiary, userSelect: 'none' }}>
              💭 {t('chat.thread.thinking')}
            </summary>
            <div style={{ fontSize: 12, color: tokens.labelSecondary,
                          lineHeight: 1.6, marginTop: 4, whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word' }}>
              {thinking}
            </div>
          </details>
        )}
        <div style={{ fontSize: 13, color: tokens.labelPrimary, lineHeight: 1.62,
                      wordBreak: 'break-word' }}>
          {rendered}
          {running && <LoadingOutlined spin style={{ marginInlineStart: 6 }} />}
        </div>
      </div>
    </div>
  );
};

const ThinkingBubble: React.FC<{ message: Message }> = ({ message }) => {
  const t = useT();
  return (
    <RoleBubble
      icon={<BulbOutlined />}
      roleLabel={t('chat.thread.thinkingRole')}
      accent="#a78bfa"
      content={stringifyContent(message.content)}
      summary={t('chat.thread.thinking')}
      collapsed
    />
  );
};

const ReviewerBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const content = stringifyContent(message.content);
  const meta = message.metadata || {};
  const score = typeof meta.score === 'number' ? meta.score : null;
  const approve = typeof meta.approve === 'boolean' ? meta.approve : null;
  return (
    <RoleBubble
      icon={<AuditOutlined />}
      accent={tokens.reviewerAccent}
      content={content}
      extra={
        (score !== null || approve !== null) ? (
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            {score !== null && (
              <Tag color={score >= 80 ? 'green' : score >= 50 ? 'orange' : 'red'}>
                {t('chat.thread.score', { n: score })}
              </Tag>
            )}
            {approve !== null && (
              <Tag color={approve ? 'green' : 'red'}>
                {approve ? t('chat.thread.approved') : t('chat.thread.changesRequested')}
              </Tag>
            )}
          </div>
        ) : null
      }
    />
  );
};

const ToolBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const meta = message.metadata || {};
  const tool = String(meta.tool || meta.name || 'tool');
  const isResult = (message.topic || '').toLowerCase() === 'tool.result';
  const body = stringifyContent(message.content);
  return (
    <RoleBubble
      icon={<ToolOutlined />}
      roleLabel={isResult
        ? t('chat.thread.toolResultRole', { tool })
        : t('chat.thread.toolLabel', { tool })}
      accent={tokens.labelTertiary}
      content={body}
      meta={message.topic}
      mono
      summary={isResult
        ? t('chat.thread.expandResult', { n: body.length })
        : undefined}
      collapsed={isResult}
    />
  );
};

const SystemBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  return (
    <div style={{ textAlign: 'center', color: tokens.labelTertiary,
                  fontSize: 12, margin: '12px 0' }}>
      {stringifyContent(message.content)}
    </div>
  );
};

const RoleBubble: React.FC<{
  icon: React.ReactNode;
  /**
   * Optional by design: a reply is identified by its mark and its chrome, not
   * by a name printed above it. Step rows keep a label, because there the
   * question "which step is this" has a real answer.
   */
  roleLabel?: string;
  accent: string;
  content: string;
  meta?: string;
  extra?: React.ReactNode;
  mono?: boolean;
  summary?: string;
  collapsed?: boolean;
}> = ({ icon, roleLabel, accent, content, meta, extra, mono, summary, collapsed }) => {
  const tokens = useThemeTokens();
  const body = (
    <div style={{
      background: tokens.agentBubble,
      border: `1px solid ${tokens.agentBubbleBorder}`,
      borderRadius: 12, padding: '10px 14px',
      fontSize: 14, lineHeight: 1.55,
      color: tokens.labelPrimary,
      fontFamily: mono ? MONO_STACK : undefined,
      whiteSpace: 'pre-wrap', wordBreak: 'break-word',
    }}>
      {content}
    </div>
  );
  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
      <div style={{
        width: 32, height: 32, borderRadius: 8,
        background: tokens.bgLay1, color: accent,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 16, flexShrink: 0, border: `1px solid ${tokens.border}`,
      }}>
        {icon}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {(roleLabel || meta) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                        marginBottom: 4 }}>
            {roleLabel && <span style={{ fontSize: 13, fontWeight: 600,
                           color: tokens.labelPrimary }}>{roleLabel}</span>}
            {meta && <span style={{ fontSize: 11, color: tokens.labelTertiary }}>
                       {meta}
                     </span>}
          </div>
        )}
        {summary ? (
          <details data-testid="collapsible-body" open={!collapsed} style={{ margin: 0 }}>
            <summary style={{ cursor: 'pointer', fontSize: 12,
                              color: tokens.labelTertiary, userSelect: 'none' }}>
              {summary}
            </summary>
            <div style={{ marginTop: 6 }}>{body}</div>
          </details>
        ) : body}
        {extra}
      </div>
    </div>
  );
};

function stringifyContent(c: Message['content']): string {
  if (typeof c === 'string') return c.replace(/^\s+/, '');
  if (c == null) return '';
  try { return JSON.stringify(c, null, 2); } catch { return String(c); }
}
