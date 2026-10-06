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
 *
 * Two exceptions get their own treatment:
 *   - the sub-agent tools (spawn_subagent / subagent_status / subagent_result)
 *     render as cards, not rows — a spawned child is its own long-running run,
 *     and "is it running / has it finished" has to be answerable at a glance
 *     (see SubagentCard);
 *   - the model's live reasoning renders as a single rolling line (see
 *     ThinkingLine), so a long train of thought never pushes the answer aside.
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
  /**
   * The call's arguments as an object, recovered before the 80-char summary
   * clamp. The sub-agent card reads ``task`` / ``background`` from here, so it
   * does not have to re-parse a truncated one-line preview.
   */
  rawArgs?: Record<string, unknown>;
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
  /**
   * Every reply in the turn, in arrival order. A turn can produce more than
   * one (a streamed answer plus a follow-up message); keeping only the first
   * and pushing the rest into `others` — which renders *above* the reply —
   * showed a turn's answers backwards.
   */
  replies: Message[];
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

/**
 * Tools that spawn or read a child agent. They are not ordinary rows: a
 * spawned child is its own little run, so each call gets a live card (see
 * ``SubagentCard``) instead of a name-and-duration line in the tool list.
 */
const SUBAGENT_TOOLS = ['spawn_subagent', 'subagent_status', 'subagent_result'];

const isSubagentStep = (s: Step): boolean =>
  s.kind === 'tool' && !!s.tool && SUBAGENT_TOOLS.includes(s.tool);

/**
 * A tool call's arguments as an object.
 *
 * Preferred source is ``metadata.args`` (what a bus that carries them sends);
 * otherwise we recover them from the ``Calling name({...})`` content the
 * backend writes. That JSON is clamped to 200 chars server-side, so a long
 * ``task`` can arrive truncated and ``JSON.parse`` can fail — hence the
 * tolerant scan for the two fields the sub-agent card actually needs.
 */
function extractToolArgs(meta: Record<string, unknown>, content: string): Record<string, unknown> {
  const fromMeta = meta?.args ?? meta?.arguments;
  if (fromMeta && typeof fromMeta === 'object' && !Array.isArray(fromMeta)) {
    return fromMeta as Record<string, unknown>;
  }
  const open = content.indexOf('(');
  const close = content.lastIndexOf(')');
  if (open < 0 || close <= open) return {};
  const inner = content.slice(open + 1, close);
  try {
    const parsed = JSON.parse(inner);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch { /* clamped JSON — fall through to the tolerant scan */ }
  const out: Record<string, unknown> = {};
  const task = inner.match(/"task"\s*:\s*"((?:[^"\\]|\\.)*)/);
  if (task) {
    out.task = task[1].replace(/\\"/g, '"').replace(/\\n/g, ' ').replace(/\\\\/g, '\\');
  }
  const bg = inner.match(/"background"\s*:\s*(true|false)/);
  if (bg) out.background = bg[1] === 'true';
  const handle = inner.match(/"handle"\s*:\s*"([^"]+)"/);
  if (handle) out.handle = handle[1];
  return out;
}

/**
 * A background handle out of a result body ("Sub-agent spawned in background.
 * handle=abc123 …"). The backend names the key, not the format, so accept
 * ``handle=`` and ``handle:``.
 */
function parseHandle(text: string): string {
  const m = (text || '').match(/\bhandle\s*[=:]\s*([A-Za-z0-9._-]+)/i);
  return m ? m[1] : '';
}

function groupIntoTurns(messages: Message[]): Turn[] {
  const turns: Turn[] = [];
  let current: Turn | null = null;
  const keyOf = (m: Message, i: number) => m.id || `i${i}`;

  messages.forEach((m, i) => {
    const sender = (m.sender || '').toLowerCase();
    if (sender === 'user' || sender === 'human') {
      current = { key: keyOf(m, i), user: m, steps: [], replies: [], others: [] };
      turns.push(current);
      return;
    }
    if (isProcessTopic(m)) {
      if (!current) {
        current = { key: keyOf(m, i), steps: [], replies: [], others: [] };
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
            // The bus reports the outcome as ``ok`` on some paths and
            // ``success`` on others (kairos/agents/base.py publishes
            // ``success``); honour both, or every failed call would read as ok.
            open.ok = (meta.ok === false || meta.success === false || meta.error)
              ? false : true;
            open.ms = Math.max(0, (m.timestamp - open.startedAt) * 1000);
            open.text = body;
          } else {
            current.steps.push({
              kind: 'tool', tool, ok: true, text: body,
              startedAt: m.timestamp,
            });
          }
        } else {
          const callBody = stringifyContent(m.content);
          current.steps.push({
            kind: 'tool', tool, ok: null,
            args: summarizeArgs(meta, callBody),
            rawArgs: extractToolArgs(meta, callBody),
            text: callBody,
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
    if (isReply(m) && current) {
      // Keep every reply, in the order the agent produced them.
      current.replies.push(m);
      return;
    }
    if (current) current.others.push(m);
    else {
      const t: Turn = { key: keyOf(m, i), steps: [], replies: [m], others: [] };
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
                  height: '100%', minHeight: 0 }}>
      {/* The blinking dots on the live thinking row. One keyframes block for
          the whole thread rather than an animation per render. */}
      <style>{THINKING_KEYFRAMES}</style>
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
          {view !== 'raw' && <LiveStatusRow messages={messages} />}
        </div>
      </div>
    </div>
  );
};

export default React.memo(ChatThread);

// ------------------------------------------------------------------ turn

const TurnViewBase: React.FC<{ turn: Turn; mdStyle: MarkdownStyle }> = ({ turn, mdStyle }) => {
  // A turn with no reply yet is still running: its process stays open so the
  // user can watch the steps land, then folds away once the answer arrives.
  // A turn is "running" only while a step is still unsettled. A tool-only turn
  // (the model calls tools and never writes a closing reply) used to count as
  // running forever, so its process block never folded and the thread read as a
  // raw log. Once every step has landed, fold to the one-line summary.
  const running = turn.replies.length === 0 && turn.steps.some((s) => s.ok === null);
  return (
    <div data-testid="chat-turn">
      {turn.user && <UserBubble message={turn.user} />}
      {turn.steps.length > 0 && <ProcessBlock steps={turn.steps} running={running} />}
      {turn.others.map((m, i) => (
        <MessageBubble key={m.id || i} message={m} mdStyle={mdStyle} />
      ))}
      {turn.replies.map((m, i) => (
        <AssistantBubble key={m.id || i} message={m} mdStyle={mdStyle}
                         running={false} />
      ))}
    </div>
  );
};

// The thread re-renders on every poll tick (every 2s) and on every streamed
// chunk, and a finished turn has nothing new to show: memo stops the markdown
// of every settled turn being walked again. `turns` and `mdStyle` are already
// memoised in this component, so the comparison actually hits.
const TurnView = React.memo(TurnViewBase);

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
  // Sub-agent calls get their own cards, shown whether the block is folded or
  // not: "is the child still running?" must not depend on the user having
  // unfolded this block. They are taken out of the ordinary row list so the
  // same call does not render twice.
  const subagentSteps = steps.filter(isSubagentStep);
  const toolSteps = steps.filter((s) => !isSubagentStep(s));
  // A digest of what the turn actually did, shown ONLY while folded: the
  // summary line then reads "思考 → read_file → grep" instead of bare "N 步",
  // and unfolding swaps it for the real step rows (which would otherwise show
  // the same tool names twice). Consecutive
  // thinking steps collapse to a single entry, and only the last four show.
  // Tool names are the backend's own identifiers and the thinking label reuses
  // chat.process.thinkingStep, so no new i18n keys are introduced.
  const seen: string[] = [];
  for (const s of steps) {
    const label = s.kind === 'thinking' ? t('chat.process.thinkingStep') : (s.tool || 'tool');
    if (seen[seen.length - 1] !== label) seen.push(label);
  }
  const digest = (seen.length > 4 ? '… → ' : '') + seen.slice(-4).join(' → ');

  return (
    <div
      data-testid="process-block"
      style={{
        margin: '2px 0 8px 0',
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
        <span style={{ flexShrink: 0 }}>
          {t('chat.process.summary', {
            n: steps.length,
            time: formatMs(total) || '—',
          })}
        </span>
        {!open && digest && (
          <span style={{
            color: tokens.labelTertiary, fontFamily: MONO_STACK,
            fontSize: 10.5, minWidth: 0, flexShrink: 1,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>{digest}</span>
        )}
        {failed > 0 && (
          <Tag color="red" style={{ marginInlineStart: 'auto', marginInlineEnd: 0,
                                    fontSize: 10, lineHeight: '16px' }}>
            {t('chat.process.failedSteps', { n: failed })}
          </Tag>
        )}
      </button>
      {subagentSteps.length > 0 && (
        <div data-testid="subagent-cards" style={{ padding: '0 10px 2px 10px' }}>
          {subagentSteps.map((s, i) => (
            // Keyed by identity, not just position: when one child finishes the
            // others must keep their component (and their ticking clock).
            <SubagentCard key={`${s.tool}-${s.startedAt}-${i}`} step={s} />
          ))}
        </div>
      )}
      {open && (
        <div style={{ padding: '0 10px 8px 10px' }}>
          {toolSteps.map((s, i) => <StepRow key={i} step={s} />)}
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

// ------------------------------------------------------------------ sub-agent

/** Characters of the delegated task shown on the card, before the ellipsis. */
const SUBAGENT_TASK_CHARS = 60;

/**
 * One sub-agent call, as a card rather than a row.
 *
 * The complaint this answers: a spawned child used to be a line in the tool
 * list indistinguishable from a file read, so "a sub-agent is running" and
 * "the sub-agent has finished" were both invisible. A card shows the delegated
 * task, a clock that ticks while the child works, and — the part that matters
 * most — an unmistakable finished/failed state.
 *
 * The clock lives in this component. Its interval is created and torn down
 * from ``running`` alone, so a sibling card finishing cannot restart or reset
 * this one.
 */
const SubagentCard: React.FC<{ step: Step }> = ({ step }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const tool = step.tool || 'spawn_subagent';
  const isSpawn = tool === 'spawn_subagent';
  const args = step.rawArgs || {};
  const background = args.background === true;
  const running = step.ok === null;
  const bad = step.ok === false;

  const task = String(args.task ?? '').replace(/\s+/g, ' ').trim();
  const taskSummary = task.length > SUBAGENT_TASK_CHARS
    ? `${task.slice(0, SUBAGENT_TASK_CHARS)}…` : task;

  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!running) return undefined;
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  const seconds = running
    ? Math.max(0, now - step.startedAt)
    : Math.max(0, (step.ms || 0) / 1000);
  const elapsed = t('market.installElapsed', { n: Math.round(seconds) });

  const handle = parseHandle(step.text) || String(args.handle ?? '');
  const report = (step.text || '').trim();
  // A background spawn returns a handle immediately while the child keeps
  // running server-side, so "the tool returned" is not "the child is done".
  const backgroundMode = isSpawn && background;

  const state = running
    ? (backgroundMode ? 'background-running' : 'running')
    : bad ? 'failed'
    : backgroundMode ? 'background' : 'done';

  const headline = bad
    ? t('common.failed')
    : running
      ? (backgroundMode ? t('chat.subagent.background')
                        : `${t('tasks.subagents')} · ${t('common.running')}`)
      : backgroundMode ? t('chat.subagent.background')
      : t('common.done');

  const accent = bad ? tokens.danger
    : running ? (tokens.coderAccent ?? tokens.brand)
    : tokens.success;
  const icon = running ? <LoadingOutlined spin style={{ fontSize: 12 }} />
    : bad ? <CloseCircleOutlined style={{ fontSize: 12 }} />
    : <CheckCircleOutlined style={{ fontSize: 12 }} />;

  return (
    <div
      data-testid="subagent-card"
      data-state={state}
      style={{
        margin: '4px 0 6px 0', padding: '6px 9px',
        border: `1px solid ${bad ? tokens.danger : tokens.border}`,
        borderInlineStart: `3px solid ${accent}`,
        borderRadius: 8,
        background: tokens.toolBubble ?? tokens.bgLay1,
        fontSize: 11.5, minWidth: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        <span style={{ color: accent, flexShrink: 0 }}>
          <BranchesOutlined style={{ fontSize: 12 }} />
        </span>
        <span style={{ color: accent, flexShrink: 0 }}>{icon}</span>
        <span data-testid="subagent-state"
              style={{ fontWeight: 600, color: tokens.labelPrimary,
                       flexShrink: 0 }}>
          {headline}
        </span>
        {!isSpawn && (
          <span style={{ fontFamily: MONO_STACK, fontSize: 10,
                         color: tokens.labelTertiary, flexShrink: 0 }}>{tool}</span>
        )}
        <span data-testid="subagent-elapsed"
              style={{ marginInlineStart: 'auto', flexShrink: 0,
                       color: tokens.labelTertiary, fontSize: 10.5 }}>
          {elapsed}
        </span>
      </div>
      {taskSummary && (
        <div data-testid="subagent-task"
             style={{ marginTop: 3, color: tokens.labelSecondary,
                      overflow: 'hidden', textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap' }}>
          {taskSummary}
        </div>
      )}
      {(backgroundMode || (!isSpawn && !!handle)) && (
        <div data-testid="subagent-handle"
             style={{ marginTop: 3, fontFamily: MONO_STACK, fontSize: 10.5,
                      color: tokens.labelTertiary }}>
          {t('chat.subagent.handle', { handle: handle || '…' })}
        </div>
      )}
      {backgroundMode && !running && (
        <div data-testid="subagent-hint"
             style={{ marginTop: 2, color: tokens.labelTertiary }}>
          {t('chat.subagent.pollHint')}
        </div>
      )}
      {bad && report && (
        <div data-testid="subagent-reason"
             style={{ marginTop: 3, color: tokens.danger,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {report.replace(/\s+/g, ' ').trim().slice(0, 200)}
        </div>
      )}
      {!running && !bad && !backgroundMode && report && (
        <details data-testid="subagent-report" style={{ marginTop: 4 }}>
          <summary style={{ cursor: 'pointer', fontSize: 11,
                            color: tokens.labelTertiary, userSelect: 'none' }}>
            {t('chat.thread.expandResult', { n: report.length })}
          </summary>
          <pre style={{
            margin: '4px 0 2px 0', padding: '6px 8px',
            background: tokens.bgLay1, color: tokens.labelSecondary,
            borderRadius: 6, fontSize: 11, lineHeight: 1.5,
            fontFamily: MONO_STACK, whiteSpace: 'pre-wrap',
            wordBreak: 'break-word', maxHeight: 240, overflow: 'auto',
          }}>{report}</pre>
        </details>
      )}
    </div>
  );
};

// ------------------------------------------------------------------ live status

/** How much of the live reasoning the single line keeps. */
const THINK_TAIL_CHARS = 140;

/** The blinking dots at the head of the thinking line. */
const THINKING_KEYFRAMES =
  '@keyframes kairos-think-blink{0%,100%{opacity:.2}50%{opacity:1}}';

/**
 * The tail of the model's live reasoning, or null when the agent is not in a
 * thinking phase.
 *
 * Source: the in-flight ``stream.chunk`` bubble — the only reasoning text the
 * UI actually receives. The provider counts hidden reasoning but does not
 * stream it (`reasoning_content` is tallied, never yielded — see
 * kairos/llm/providers/openai_provider.py), so what is available to show is
 * the ``<think>`` block the Coder is asked to write into its own reply. While
 * that block is open the body has not started: that is the thinking phase, and
 * its last ~140 characters are what the single-line row shows.
 */
function liveThinkingTail(messages: Message[]): string | null {
  const last = messages[messages.length - 1];
  if (!last) return null;
  // Only while a stream is in flight. Once it is finalized (topic renamed to
  // stream.complete) or a tool call / reply lands after it, the phase is over.
  if ((last.topic || '').toLowerCase() !== 'stream.chunk') return null;
  const raw = stringifyContent(last.content);
  const open = raw.match(/<think>/i);
  if (!open || open.index === undefined) return null;
  if (/<\/think>/i.test(raw)) return null;   // the answer is starting
  const thinking = raw.slice(open.index + open[0].length).replace(/\s+/g, ' ').trim();
  if (!thinking) return null;
  return thinking.slice(-THINK_TAIL_CHARS);
}

/**
 * The live reasoning as ONE line: a blinking "thinking" marker and the tail of
 * the stream, clipped on the left so new text slides in from the right (a
 * terminal status line, not a paragraph). It is muted and small on purpose —
 * supporting information that must not compete with the answer.
 */
const ThinkingLine: React.FC<{ text: string }> = ({ text }) => {
  const tokens = useThemeTokens();
  const t = useT();
  return (
    <div
      data-testid="thinking-line"
      style={{ display: 'flex', alignItems: 'center', gap: 8,
               margin: '10px 0 6px', fontSize: 12, minWidth: 0,
               color: tokens.labelTertiary }}
    >
      <span aria-hidden data-testid="thinking-dots"
            style={{ display: 'inline-flex', gap: 3, flexShrink: 0 }}>
        {[0, 1, 2].map((i) => (
          <span key={i} style={{
            width: 3, height: 3, borderRadius: '50%',
            background: tokens.labelTertiary,
            animation: 'kairos-think-blink 1.2s ease-in-out infinite',
            animationDelay: `${i * 0.2}s`,
          }} />
        ))}
      </span>
      <span style={{ flexShrink: 0 }}>{t('loop.agentStatus.thinking')}</span>
      <span
        data-testid="thinking-line-text"
        style={{
          display: 'flex', justifyContent: 'flex-end', overflow: 'hidden',
          whiteSpace: 'nowrap', minWidth: 0, flex: 1, opacity: 0.9,
          fontFamily: MONO_STACK, fontSize: 11.5,
        }}
      >
        <span style={{ whiteSpace: 'nowrap' }}>{text}</span>
      </span>
    </div>
  );
};

const LiveStatusRow: React.FC<{ messages: Message[] }> = ({ messages }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const live = useChatStore((s) => s.liveStatus);
  const streamTail = useMemo(() => liveThinkingTail(messages), [messages]);
  if (!live) return null;
  // Thinking phase with reasoning to show: one rolling line instead of the
  // generic status row. The preferred source is the `agent.thinking` tail the
  // backend now streams onto ``liveStatus.text``; the `<think>`-block heuristic
  // stays as the fallback for providers that only expose reasoning inside the
  // reply body. Every other state (tool running, no reasoning yet, the turn
  // over) falls through to the row below.
  if (live.kind === 'thinking') {
    const fromThinking = (live.text || '').replace(/\s+/g, ' ').trim();
    const tail = fromThinking
      ? fromThinking.slice(-THINK_TAIL_CHARS)
      : streamTail;
    if (tail) return <ThinkingLine text={tail} />;
  }
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
  const closed = text.match(/<think>([\s\S]*?)<\/think>/i);
  if (closed) {
    return { thinking: (closed[1] || '').trim(),
             body: text.replace(closed[0], '').trim() };
  }
  // An OPEN <think> — still streaming. Everything so far is reasoning and the
  // body has not started, so nothing belongs in the answer yet. Without this
  // the raw "<think>…" text rendered as the reply and competed with it; the
  // single-line live row is where streaming reasoning belongs.
  const open = text.match(/<think>([\s\S]*)$/i);
  if (open) return { thinking: (open[1] || '').trim(), body: '' };
  return { thinking: '', body: text };
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
    <div data-testid="assistant-bubble" style={{ marginTop: 2, marginBottom: 4 }}>
      <div style={{ background: tokens.bgElevated,
                    borderRadius: 8, padding: '7px 11px' }}>
        {tokensUsed !== null && (
          <div style={{ display: 'flex', justifyContent: 'flex-end',
                        marginBottom: 2 }}>
            <Tooltip title={t('chat.thread.tokenUsage')}>
              <span style={{ fontSize: 10, color: tokens.labelTertiary }}>
                {t('chat.thread.tokensUsed', { n: tokensUsed.toLocaleString() })}
              </span>
            </Tooltip>
          </div>
        )}
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
      borderRadius: 8, padding: '7px 10px',
      fontSize: 13, lineHeight: 1.5,
      color: tokens.labelPrimary,
      fontFamily: mono ? MONO_STACK : undefined,
      whiteSpace: 'pre-wrap', wordBreak: 'break-word',
    }}>
      {content}
    </div>
  );
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
      <div style={{
        width: 22, height: 22, borderRadius: 6,
        background: tokens.bgLay1, color: accent,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 12, flexShrink: 0, border: `1px solid ${tokens.border}`,
      }}>
        {icon}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {(roleLabel || meta) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6,
                        marginBottom: 2 }}>
            {roleLabel && <span style={{ fontSize: 12, fontWeight: 600,
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
