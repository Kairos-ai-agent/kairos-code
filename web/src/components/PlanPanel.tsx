/**
 * PlanPanel — live TodoWrite-style plan checklist.
 *
 * Subscribes to ``plan.updated`` events on the WebSocket and
 * renders the agent's current plan as a checklist. The plan is
 * produced by the Coder agent's ``write_todos`` tool call
 * (intercepted by the backend in R11.1) and pushed on every
 * diff as a ``plan.updated`` event with the full plan state in
 * ``metadata.plan``.
 *
 * UI behavior:
 *  - [x] items: completed (gray + strike-through)
 *  - [>] items: in_progress (blue, animated)
 *  - [ ] items: pending (gray)
 *  - When the agent is acting on a tool, the in_progress row
 *    shows the activeForm ("Doing X…") instead of the static
 *    "content"
 *  - Completion ratio (3/5) + progress bar at the top
 *  - Compact mode: just the count + a chevron to expand
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Tag, Tooltip, Progress, Collapse, Empty } from 'antd';
import {
  CheckCircleOutlined, PlayCircleOutlined, MinusCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';
import type { Message } from '../types';

interface TodoItem {
  status: 'pending' | 'in_progress' | 'completed';
  content: string;
  activeForm?: string;
}

interface Plan {
  todos: TodoItem[];
  updated_at?: number;
}

interface Props {
  /** Live messages from the WebSocket. We filter for ``plan.updated``. */
  messages: Message[];
}

function parsePlanFromMessage(msg: Message): Plan | null {
  if (msg.topic !== 'plan.updated') return null;
  const m = msg.metadata as { plan?: Plan } | undefined;
  if (!m || !m.plan || !Array.isArray(m.plan.todos)) return null;
  return m.plan;
}

function statusIcon(status: TodoItem['status']) {
  if (status === 'completed') return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
  if (status === 'in_progress') return <PlayCircleOutlined style={{ color: '#1677ff' }} />;
  return <MinusCircleOutlined style={{ color: '#bfbfbf' }} />;
}

function statusKey(status: TodoItem['status']): string {
  if (status === 'completed') return 'plan.status.completed';
  if (status === 'in_progress') return 'plan.status.inProgress';
  return 'plan.status.pending';
}

const PlanPanel: React.FC<Props> = ({ messages }) => {
  const tokens = useThemeTokens();
  const t = useT();
  // The most-recent plan.updated event wins.
  const plan = useMemo<Plan | null>(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const p = parsePlanFromMessage(messages[i]);
      if (p) return p;
    }
    return null;
  }, [messages]);

  const [compact, setCompact] = useState(false);

  if (!plan || plan.todos.length === 0) {
    return (
      <div
        style={{
          padding: '8px 12px',
          border: `1px dashed ${tokens.border}`,
          borderRadius: 6,
          background: tokens.bgLay1,
          color: tokens.labelSecondary,
          fontSize: 12,
        }}
      >
        <ThunderboltOutlined style={{ marginInlineEnd: 6, color: tokens.brand }} />
        {t('plan.empty')}
      </div>
    );
  }

  const completed = plan.todos.filter((item) => item.status === 'completed').length;
  const total = plan.todos.length;
  const pct = total === 0 ? 0 : Math.round((completed / total) * 100);
  const current = plan.todos.find((item) => item.status === 'in_progress');

  if (compact) {
    return (
      <Tooltip title={t('plan.expandTooltip')}>
        <Tag
          color="blue"
          onClick={() => setCompact(false)}
          style={{ cursor: 'pointer', userSelect: 'none' }}
        >
          <ThunderboltOutlined /> {t('plan.progress', { done: completed, total })}
          {current ? ` — ${current.activeForm || current.content}` : ''}
        </Tag>
      </Tooltip>
    );
  }

  return (
    <div
      style={{
        border: `1px solid ${tokens.border}`,
        borderRadius: 6,
        background: tokens.bgLay1,
        padding: '10px 12px',
        fontSize: 13,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ThunderboltOutlined style={{ color: tokens.brand }} />
          <span style={{ fontWeight: 600, color: tokens.labelPrimary }}>
            {t('loop.plan.viz')}
          </span>
          <Tag color="blue" style={{ marginInlineStart: 4 }}>
            {completed}/{total}
          </Tag>
        </div>
        <a
          onClick={() => setCompact(true)}
          style={{
            cursor: 'pointer',
            color: tokens.labelSecondary,
            fontSize: 11,
            userSelect: 'none',
          }}
        >
          {t('plan.compact')}
        </a>
      </div>
      <Progress
        percent={pct}
        showInfo={false}
        size="small"
        strokeColor={tokens.brand}
      />
      <ul
        style={{
          listStyle: 'none',
          padding: 0,
          margin: '8px 0 0 0',
        }}
      >
        {plan.todos.map((item, idx) => {
          const isCurrent = item.status === 'in_progress';
          const display = isCurrent && item.activeForm ? item.activeForm : item.content;
          return (
            <li
              key={`${item.content}-${idx}`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '3px 0',
                color: item.status === 'completed'
                  ? tokens.labelSecondary
                  : tokens.labelPrimary,
                textDecoration: item.status === 'completed' ? 'line-through' : 'none',
                opacity: item.status === 'completed' ? 0.7 : 1,
                animation: isCurrent ? 'pulse 1.5s ease-in-out infinite' : undefined,
              }}
            >
              {statusIcon(item.status)}
              <span style={{ flex: 1 }}>{display}</span>
              {isCurrent && (
                <Tag color="processing" style={{ marginInlineStart: 'auto', fontSize: 10 }}>
                  {t('plan.working')}
                </Tag>
              )}
              <Tooltip title={t(statusKey(item.status))}>
                <span style={{ display: 'none' }}>{item.status}</span>
              </Tooltip>
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default PlanPanel;
