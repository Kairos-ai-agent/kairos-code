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

  // Auto-scroll on new messages.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !stickToBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
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
  const tokens = useThemeTokens();
  return (
    <RoleBubble
      icon={<CodeOutlined />}
      roleLabel="Coder"
      accent={tokens.coderAccent}
      content={stringifyContent(message.content)}
      meta={message.topic}
    />
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
  if (typeof c === 'string') return c;
  if (c == null) return '';
  try { return JSON.stringify(c, null, 2); } catch { return String(c); }
}
