/**
 * ChatThread — the scrollable message list in the main area.
 *
 * Message rendering rules (mirrors the legacy Loop page but as a
 * scrollable list of bubbles, not a tabbed inspector):
 *
 *   - User: light gray bubble, right-aligned, plain text
 *   - Coder / Reviewer: bordered card with a role chip + name + content
 *   - Tool call: code-style block with a "tool" pill
 *   - Tool result: code-style block, dim, indented under the call
 *   - Loop digest: prominent score + verdict block
 *   - Issue: severity-colored card (CRITICAL/MAJOR/MINOR/SUGGESTION)
 *
 * Auto-scrolls to the bottom on new messages unless the user has
 * scrolled up to read history (then leaves the scroll position
 * alone — standard chat behaviour).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Tag, Empty, Badge, Tooltip, Segmented } from 'antd';
import {
  UserOutlined, CodeOutlined, AuditOutlined, ToolOutlined,
  CheckCircleOutlined, CloseCircleOutlined,
  BranchesOutlined, FileTextOutlined, BulbOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import type { Message } from '../types';

interface Props {
  messages: Message[];
  emptyHint?: string;
  /** Show a "raw" toggle to dump the raw JSON instead of the parsed view. */
  showRawToggle?: boolean;
}

const ChatThread: React.FC<Props> = ({ messages, emptyHint, showRawToggle = false }) => {
  const tokens = useThemeTokens();
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

  // Auto-scroll on new messages. R38.6.4: the previous logic
  // set ``el.scrollTop = el.scrollHeight`` synchronously, but
  // the DOM hadn't reflowed the new bubble yet, so the
  // scrollHeight was the OLD value and the new bottom was
  // either missed (tall messages cut off the top) or jumped
  // somewhere wrong. Use ``requestAnimationFrame`` so the
  // scroll happens after React has painted the new message,
  // and fall back to ``scrollIntoView`` on the last child
  // for the rare case where the container has a different
  // scrollable parent (e.g. nested flex with overflow:hidden).
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

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      height: 'calc(100vh - 52px)',
    }}>
      {showRawToggle && (
        <div style={{
          display: 'flex', justifyContent: 'flex-end',
          padding: '8px 16px 0',
        }}>
          <Segmented
            size="small"
            value={view}
            onChange={(v) => setView(v as 'pretty' | 'raw')}
            options={[{ label: 'Pretty', value: 'pretty' },
                       { label: 'Raw', value: 'raw' }]}
          />
        </div>
      )}
      <div
        ref={containerRef}
        style={{
          flex: 1, overflowY: 'auto',
          padding: '24px 16px 24px',
        }}
      >
        <div style={{ maxWidth: 768, margin: '0 auto' }}>
          {messages.length === 0 ? (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              minHeight: '60vh',
            }}>
              <Empty
                image={<BranchesOutlined style={{ fontSize: 40,
                                                color: tokens.labelTertiary }} />}
                description={
                  <span style={{ color: tokens.labelTertiary }}>
                    {emptyHint || 'Send a message to start the loop.'}
                  </span>
                }
              />
            </div>
          ) : view === 'raw' ? (
            <pre style={{
              background: tokens.toolBubble, color: tokens.labelPrimary,
              padding: 16, borderRadius: 8, overflow: 'auto',
              fontSize: 12, lineHeight: 1.5,
              fontFamily: 'SF Mono, "JetBrains Mono", Consolas, monospace',
            }}>
              {JSON.stringify(messages, null, 2)}
            </pre>
          ) : (
            messages.map((m, i) => (
              <MessageBubble key={m.id || i} message={m} />
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default ChatThread;

// ---------------------------------------------------------------------------
// MessageBubble — render one Message according to its sender/topic/type.
// ---------------------------------------------------------------------------

const MessageBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  const role = inferRole(message);
  if (role === 'user') {
    return <UserBubble message={message} />;
  }
  if (role === 'tool') {
    return <ToolBubble message={message} />;
  }
  if (role === 'reviewer') {
    return <ReviewerBubble message={message} />;
  }
  if (role === 'coder') {
    return <CoderBubble message={message} />;
  }
  // Generic system / unknown.
  return <SystemBubble message={message} />;
};

function inferRole(m: Message): 'user' | 'coder' | 'reviewer' | 'tool' | 'system' {
  const sender = (m.sender || '').toLowerCase();
  const topic = (m.topic || '').toLowerCase();
  if (sender === 'user' || sender === 'human') return 'user';
  if (sender.includes('reviewer') || topic.includes('review')) return 'reviewer';
  if (sender.includes('tool')) return 'tool';
  if (sender.includes('coder') || sender.includes('planner')
      || sender.includes('specialist') || sender === 'agent') return 'coder';
  return 'system';
}

const UserBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end',
                  marginBottom: 16 }}>
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

const CoderBubble: React.FC<{ message: Message }> = ({ message }) => {
  // R38.6.3: the user said the "Coder" role label is noisy
  // in casual chat. Render the reply as a plain conversation
  // bubble (small Kairos avatar + text) instead of a bordered
  // role card. We keep the Reviewer card because its status
  // (verdict, score) is useful info the user wants to see.
  return <AssistantBubble message={message} />;
};

function splitThinking(text: string): { thinking: string; body: string } {
  if (!text) return { thinking: '', body: text };
  const m = text.match(/<think>([\s\S]*?)<\/think>/i);
  if (!m) return { thinking: '', body: text };
  return { thinking: (m[1] || '').trim(),
           body: text.replace(m[0], '').trim() };
}

// R38.6.3: new "AssistantBubble" — a clean conversation-style
// bubble used for plain chat replies. Small avatar (the Kairos
// K), a thin label ("Kairos"), and the text. No bordered card
// or topic meta line — chat should feel like chat.
const AssistantBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  const { thinking, body } = splitThinking(stringifyContent(message.content));
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 8,
      marginTop: 4, marginBottom: 4,
    }}>
      <div style={{
        width: 26, height: 26, borderRadius: 6,
        background: '#facc15', color: '#000',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontWeight: 900, fontSize: 14, flexShrink: 0, marginTop: 2,
      }}>K</div>
      <div style={{
        flex: 1, minWidth: 0,
        background: tokens.bgElevated, borderRadius: 8,
        padding: '8px 12px',
      }}>
        <span style={{
          fontSize: 11, color: tokens.labelTertiary,
          display: 'block', marginBottom: 2,
        }}>Kairos</span>
        {thinking && (
          <details
            data-testid="think-block"
            style={{
              marginBottom: 6, background: tokens.bgLay1,
              border: `1px solid ${tokens.border}`,
              borderRadius: 6, padding: '4px 8px',
            }}
          >
            <summary style={{
              cursor: 'pointer', fontSize: 11,
              color: tokens.labelTertiary, userSelect: 'none',
            }}>
              💭 思考过程（点击展开）
            </summary>
            <div style={{
              fontSize: 12, color: tokens.labelSecondary,
              lineHeight: 1.6, marginTop: 4,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {thinking}
            </div>
          </details>
        )}
        {body && (
          <span style={{
            fontSize: 13, color: tokens.labelPrimary,
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            lineHeight: 1.6,
          }}>{body}</span>
        )}
      </div>
    </div>
  );
};

const ReviewerBubble: React.FC<{ message: Message }> = ({ message }) => {
  const tokens = useThemeTokens();
  const content = stringifyContent(message.content);
  // Try to extract a score / verdict from the metadata if present.
  const meta = message.metadata || {};
  const score = typeof meta.score === 'number' ? meta.score : null;
  const approve = typeof meta.approve === 'boolean' ? meta.approve : null;
  return (
    <RoleBubble
      icon={<AuditOutlined />}
      roleLabel="Reviewer"
      accent={tokens.reviewerAccent}
      content={content}
      meta={message.topic}
      extra={
        (score !== null || approve !== null) ? (
          <div style={{ display: 'flex', gap: 8, marginTop: 10,
                        flexWrap: 'wrap' }}>
            {score !== null && (
              <Tag color={score >= 80 ? 'green' : score >= 50 ? 'orange' : 'red'}>
                Score {score}
              </Tag>
            )}
            {approve !== null && (
              <Tag color={approve ? 'green' : 'red'}>
                {approve ? 'Approved' : 'Changes requested'}
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
  const meta = message.metadata || {};
  const tool = meta.tool || meta.name || 'tool';
  return (
    <RoleBubble
      icon={<ToolOutlined />}
      roleLabel={`tool · ${tool}`}
      accent={tokens.labelTertiary}
      content={stringifyContent(message.content)}
      meta={message.topic}
      mono
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
  roleLabel: string;
  accent: string;
  content: string;
  meta?: string;
  extra?: React.ReactNode;
  mono?: boolean;
}> = ({ icon, roleLabel, accent, content, meta, extra, mono }) => {
  const tokens = useThemeTokens();
  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
      <div style={{
        width: 32, height: 32, borderRadius: 8,
        background: tokens.bgLay1, color: accent,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 16, flexShrink: 0,
        border: `1px solid ${tokens.border}`,
      }}>
        {icon}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          marginBottom: 4,
        }}>
          <span style={{ fontSize: 13, fontWeight: 600,
                        color: tokens.labelPrimary }}>{roleLabel}</span>
          {meta && <span style={{ fontSize: 11, color: tokens.labelTertiary }}>
                     {meta}
                   </span>}
        </div>
        <div style={{
          background: tokens.agentBubble,
          border: `1px solid ${tokens.agentBubbleBorder}`,
          borderRadius: 12, padding: '10px 14px',
          fontSize: 14, lineHeight: 1.55,
          color: tokens.labelPrimary,
          fontFamily: mono
            ? 'SF Mono, "JetBrains Mono", Consolas, monospace'
            : undefined,
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
          {content}
        </div>
        {extra}
      </div>
    </div>
  );
};

function stringifyContent(c: Message['content']): string {
  if (typeof c === 'string') {
    // R38.6.4: the LLM reply sometimes starts with "\n" or " "
    // (a stray newline from a markdown code block, or the API
    // returning a leading blank line). With whiteSpace: pre-wrap
    // in the bubble that renders as a visible empty first line.
    // Strip leading whitespace so the bubble starts tight.
    return c.replace(/^\s+/, '');
  }
  if (c == null) return '';
  try { return JSON.stringify(c, null, 2); } catch { return String(c); }
}

